"""Deterministic incident report + validation of the agent's recommendation."""
from __future__ import annotations

import re
from typing import Any

from .. import config, db
from ..physics.maneuver import ASSUMPTIONS

RECOMMENDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "risk_level": {"type": "string", "enum": ["HIGH", "ELEVATED", "LOW"]},
        "recommended_action": {
            "type": "string",
            "enum": ["PREPARE_MANEUVER_FOR_APPROVAL", "MONITOR_AND_REQUEST_TRACKING", "NO_ACTION"],
        },
        "recommended_maneuver_id": {"type": "string", "description": "e.g. 'M2', or empty string if none"},
        "rationale": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["claim", "evidence"],
                "additionalProperties": False,
            },
        },
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "risk_level", "recommended_action", "recommended_maneuver_id", "rationale", "uncertainties", "next_steps"],
    "additionalProperties": False,
}

LIMITATIONS = [
    "Close-approach SCREENING based on SGP4 point estimates; this is not a validated collision probability (Pc) assessment.",
    "TLE/SGP4 position errors are typically ~1 km at epoch and grow with TLE age; no covariance data is available.",
    *ASSUMPTIONS,
    "Advisory output only. OrbitalGuard has no capability to command any spacecraft.",
]


def build_report(ctx) -> dict:
    m, ev = ctx.mission, ctx.event
    p, s = m.obj(ev["primary_id"]), m.obj(ev["secondary_id"])
    ca = None
    for eid, e in ctx.evidence.items():
        if e["tool"] == "analyze_close_approach" and set(e["output"]["pair"]) == {p.id, s.id}:
            ca = {"evidence_id": eid, **(e["output"].get("closest") or {})}
    comparison = ctx.comparison
    if comparison is None and ctx.maneuvers:
        from .tools import compare

        comparison = compare(ctx, list(ctx.maneuvers), config.DEFAULT_TARGET_MISS_KM)
    return {
        "report_id": f"RPT-{ctx.id}",
        "generated_at": db.now_iso(),
        "data_label": ev["data_label"],
        "event_summary": {
            "event_id": ev["id"],
            "primary": f"{p.name} ({p.id})",
            "secondary": f"{s.name} ({s.id})",
            "tca_utc": ev["tca_utc"],
            "miss_km": ev["miss_km"],
            "rel_speed_km_s": ev["rel_speed_km_s"],
            "miss_ric_km": ev["miss_ric_km"],
            "severity": ev["severity"],
            "detected_by": ev["detected_by"],
        },
        "objects": [
            {**m.object_summary(o), "position_sigma_km_at_tca": round(m.position_sigma_km(o, ev["tca_s"]), 3)} for o in (p, s)
        ],
        "data_sources": {
            "catalog": m.data_info.get("source"),
            "scenario": m.scenario_id,
            "scenario_epoch": m.epoch.iso(),
            "freshness": {o.name: f"TLE age {m.tle_age_days(o):.2f} d at scenario epoch" for o in (p, s)},
        },
        "analyses_performed": [
            {"evidence_id": eid, "tool": e["tool"], "input": e["input"]} for eid, e in ctx.evidence.items()
        ],
        "close_approach_analysis": ca,
        "maneuver_comparison": comparison,
        "limitations": LIMITATIONS,
        "approval": {"required": True, "status": "PENDING"},
    }


_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?)?|\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b(?:\s*UTC)?")
_NUM = re.compile(r"(?<![\w.\-])(\d+(?:\.\d+)?)")
_FACTORS = (1.0, 1000.0, 0.001, 60.0, 1 / 60.0, 3600.0, 1 / 3600.0)


def _leaves(x: Any, out: list[float]) -> list[float]:
    if isinstance(x, bool):
        return out
    if isinstance(x, (int, float)):
        out.append(float(x))
    elif isinstance(x, dict):
        for v in x.values():
            _leaves(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _leaves(v, out)
    return out


def _matches(value: float, decimals: int, leaves: list[float]) -> bool:
    tol_abs = 0.5 * 10 ** (-decimals) if decimals else 0.5
    for leaf in leaves:
        for f in _FACTORS:
            cand = abs(leaf * f)
            if abs(cand - value) <= max(tol_abs, 0.02 * abs(value)):
                return True
    return False


def validate_recommendation(ctx, rec: dict) -> dict:
    """Hard errors make the recommendation invalid; warnings are surfaced to the operator."""
    errors, warnings, claims = [], [], []
    for item in rec.get("rationale", []):
        cited = item.get("evidence", [])
        unknown = [c for c in cited if c not in ctx.evidence]
        if unknown:
            errors.append(f"claim cites unknown evidence {unknown}")
        leaves: list[float] = []
        for c in cited:
            if c in ctx.evidence:
                _leaves(ctx.evidence[c]["output"], leaves)
        unverified = []
        claim_text = _TIMESTAMP.sub(" ", item.get("claim", ""))
        for mt in _NUM.finditer(claim_text):
            txt = mt.group(1)
            val = float(txt)
            dec = len(txt.split(".")[1]) if "." in txt else 0
            if dec == 0 and val < 10:
                continue  # small counts ("2 options", "1 orbit")
            if not _matches(val, dec, leaves):
                unverified.append(txt)
        if not cited:
            warnings.append(f"uncited claim: \"{item.get('claim', '')[:80]}\"")
        claims.append({"claim": item.get("claim"), "evidence": cited, "unverified_numbers": unverified,
                       "status": "verified" if cited and not unknown and not unverified else "check"})
    action, mid = rec.get("recommended_action"), rec.get("recommended_maneuver_id") or ""
    if action == "PREPARE_MANEUVER_FOR_APPROVAL":
        if mid not in ctx.maneuvers:
            errors.append(f"recommended_maneuver_id '{mid}' was never simulated")
        elif ctx.comparison and ctx.comparison.get("best_option") not in (None, mid):
            warnings.append(f"recommended {mid} differs from the comparison's best option {ctx.comparison.get('best_option')}")
        elif ctx.maneuvers[mid]["secondary_conjunctions"]:
            warnings.append(f"{mid} creates new conjunctions")
    elif mid and mid not in ctx.maneuvers:
        errors.append(f"recommended_maneuver_id '{mid}' was never simulated")
    if ctx.report is None:
        warnings.append("agent did not call generate_incident_report; compiled automatically")
    return {"valid": not errors, "errors": errors, "warnings": warnings, "claims": claims}
