"""Agent tool layer.

Every tool is a thin, validated wrapper around the deterministic physics engine.
Each call is executed with a timeout, logged as an evidence step (E1, E2, ...)
and its JSON result is what the LLM sees. The LLM never produces numbers that
are not traceable to one of these outputs (enforced in submit_recommendation).
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

import numpy as np
from pydantic import BaseModel, Field, ValidationError

from ..orbital.catalog import CatalogError, element_freshness
from ..orbital.maneuver import ManeuverPlan, compare_maneuvers as _compare, simulate_maneuver as _simulate
from ..orbital.screening import closest_approach
from ..orbital.timeutil import add_s, iso, parse_iso
from ..world import World

TOOL_TIMEOUT_S = 25.0
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")

LIMITATIONS = [
    "TLE/SGP4 screening only: TLEs carry no covariance, so no probability of collision (Pc) is computed.",
    "Position uncertainty values are rule-of-thumb estimates for SGP4 in LEO, not a covariance analysis.",
    "Maneuver results come from a simplified impulsive model (SGP4 + two-body/J2 differential correction).",
    "Results must be confirmed with higher-accuracy ephemerides (e.g. operator data, CDMs) before any action.",
]


# ---------------------------------------------------------------------------
# argument models
# ---------------------------------------------------------------------------

class SatInfoArgs(BaseModel):
    satellite_id: str


class TrajectoryArgs(BaseModel):
    satellite_id: str
    start_min: float = Field(0, ge=0, le=1440)
    duration_min: float = Field(120, gt=0, le=720)
    step_min: float = Field(10, ge=1, le=60)


class CloseApproachArgs(BaseModel):
    satellite_a: str
    satellite_b: str
    window_start_min: Optional[float] = Field(None, ge=0)
    window_end_min: Optional[float] = Field(None, gt=0)


class ManeuverArgs(BaseModel):
    satellite_id: str
    event_id: str
    burn_lead_time_min: float = Field(gt=0, le=720, description="minutes before the event TCA")
    dv_along_track_m_s: float = Field(0.0, ge=-2, le=2)
    dv_radial_m_s: float = Field(0.0, ge=-2, le=2)
    dv_normal_m_s: float = Field(0.0, ge=-2, le=2)
    label: str = ""


class CompareArgs(BaseModel):
    maneuver_ids: list[str] = Field(min_length=1, max_length=12)
    min_safe_miss_km: float = Field(1.0, ge=0.2, le=10)


class ReportArgs(BaseModel):
    event_id: str


class RecommendationArgs(BaseModel):
    decision: Literal["maneuver", "monitor_only", "escalate"]
    recommended_maneuver_id: Optional[str] = None
    summary: str = Field(min_length=20, max_length=1500)
    rationale: list[str] = Field(min_length=1, max_length=8)
    evidence_refs: list[str] = Field(min_length=1, description="evidence step ids such as E3")
    risks_and_limitations: list[str] = Field(min_length=1, max_length=8)
    next_steps: list[str] = Field(min_length=1, max_length=8)
    confidence: Literal["low", "medium", "high"]


# ---------------------------------------------------------------------------
# OpenAI-format tool schemas shown to the model
# ---------------------------------------------------------------------------

def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
    }}


TOOL_SCHEMAS = [
    _fn("get_satellite_info",
        "Orbital elements, current state, operator, maneuverability, data source and element age/freshness "
        "for one tracked object.",
        {"satellite_id": {"type": "string"}}, ["satellite_id"]),
    _fn("get_trajectory",
        "SGP4-predicted trajectory samples (altitude, lat/lon, speed) plus summary statistics. "
        "Times are minutes after scenario start.",
        {"satellite_id": {"type": "string"},
         "start_min": {"type": "number", "description": "default 0"},
         "duration_min": {"type": "number", "description": "max 720, default 120"},
         "step_min": {"type": "number", "description": "1-60, default 10"}},
        ["satellite_id"]),
    _fn("analyze_close_approach",
        "Deterministic closest approach between two objects in a time window (default: full screening horizon): "
        "TCA, miss distance, radial/in-track/cross-track components, relative speed, element ages and "
        "rule-of-thumb position uncertainty. Does NOT compute collision probability.",
        {"satellite_a": {"type": "string"}, "satellite_b": {"type": "string"},
         "window_start_min": {"type": "number"}, "window_end_min": {"type": "number"}},
        ["satellite_a", "satellite_b"]),
    _fn("simulate_maneuver",
        "Simulate ONE hypothetical impulsive burn for a maneuverable satellite against an event. Returns new miss "
        "distance, delta-v, propellant estimate, orbit change and a re-screen against ALL other objects "
        "(secondary conjunctions). Use dv 0 for a 'no maneuver' baseline. Along-track +=prograde, -=retrograde. "
        "Limits: |dv| <= 2 m/s, burn >= 10 min after now and >= 1 min before TCA.",
        {"satellite_id": {"type": "string"}, "event_id": {"type": "string"},
         "burn_lead_time_min": {"type": "number", "description": "minutes before TCA (one orbit is ~95 min)"},
         "dv_along_track_m_s": {"type": "number"}, "dv_radial_m_s": {"type": "number"},
         "dv_normal_m_s": {"type": "number"}, "label": {"type": "string"}},
        ["satellite_id", "event_id", "burn_lead_time_min", "dv_along_track_m_s"]),
    _fn("compare_maneuvers",
        "Rank simulated maneuvers with deterministic criteria (clears the threat by min_safe_miss_km, creates or "
        "worsens no RED/AMBER secondary conjunction, then lowest delta-v).",
        {"maneuver_ids": {"type": "array", "items": {"type": "string"}},
         "min_safe_miss_km": {"type": "number", "description": "default 1.0"}},
        ["maneuver_ids"]),
    _fn("generate_incident_report",
        "Compile the evidence-based incident report for the event from all tool results gathered so far. "
        "Call after comparing maneuvers and before submit_recommendation.",
        {"event_id": {"type": "string"}}, ["event_id"]),
    _fn("submit_recommendation",
        "Final step. Submit your structured recommendation for HUMAN review. Every number you write must come "
        "from a tool output; cite evidence ids (E#). A 'maneuver' decision must reference a maneuver_id that "
        "compare_maneuvers marked acceptable. This does not execute anything.",
        {"decision": {"type": "string", "enum": ["maneuver", "monitor_only", "escalate"]},
         "recommended_maneuver_id": {"type": ["string", "null"]},
         "summary": {"type": "string", "description": "2-4 sentences for the flight director"},
         "rationale": {"type": "array", "items": {"type": "string"}},
         "evidence_refs": {"type": "array", "items": {"type": "string"}},
         "risks_and_limitations": {"type": "array", "items": {"type": "string"}},
         "next_steps": {"type": "array", "items": {"type": "string"}},
         "confidence": {"type": "string", "enum": ["low", "medium", "high"]}},
        ["decision", "summary", "rationale", "evidence_refs", "risks_and_limitations", "next_steps", "confidence"]),
]


# ---------------------------------------------------------------------------
# execution context
# ---------------------------------------------------------------------------

@dataclass
class Context:
    world: World
    inv_id: str
    event: dict
    report: Optional[dict] = None
    recommendation: Optional[dict] = None
    last_comparison: Optional[dict] = None
    grounding_rejections: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)  # "E3" -> result

    @property
    def db(self):
        return self.world.db


def _uncertainty_km(age_hours: float) -> float:
    """Rule of thumb for SGP4/TLE in LEO: ~1 km at epoch, growing ~2 km/day (illustrative, not covariance)."""
    return round(1.0 + 2.0 * abs(age_hours) / 24.0, 2)


def _brief(obj) -> dict:
    return {"id": obj.id, "name": obj.name, "kind": obj.kind, "source": obj.source, "simulated": obj.simulated}


# ---------------------------------------------------------------------------
# tool implementations
# ---------------------------------------------------------------------------

def get_satellite_info(ctx: Context, a: SatInfoArgs) -> dict:
    w = ctx.world
    obj = w.catalog.get(a.satellite_id)
    out = obj.summary(w.sim_now())
    out["freshness"] = element_freshness(obj, w.sim_now())
    out["data_source_detail"] = w.catalog.source_detail
    out["meta"] = obj.meta
    return out


def get_trajectory(ctx: Context, a: TrajectoryArgs) -> dict:
    w = ctx.world
    w.catalog.get(a.satellite_id)
    tr = w.catalog.trajectory(a.satellite_id, add_s(w.t0, a.start_min * 60), a.duration_min, a.step_min * 60)
    alt = np.array(tr["alt_km"])
    n = len(alt)
    idx = np.unique(np.linspace(0, n - 1, min(n, 24)).astype(int)) if n else []
    return {
        "satellite_id": a.satellite_id,
        "window_min": [a.start_min, a.start_min + a.duration_min],
        "samples": [{"t_min": round(a.start_min + tr["offsets_s"][i] / 60, 1), "alt_km": tr["alt_km"][i],
                     "lat_deg": tr["lat_deg"][i], "lon_deg": tr["lon_deg"][i], "speed_km_s": tr["speed_km_s"][i]}
                    for i in idx],
        "stats": {"min_alt_km": float(alt.min()) if n else None, "max_alt_km": float(alt.max()) if n else None,
                  "mean_speed_km_s": round(float(np.mean(tr["speed_km_s"])), 4) if n else None},
        "propagation_errors": tr["propagation_errors"],
        "model": "SGP4",
    }


def analyze_close_approach(ctx: Context, a: CloseApproachArgs) -> dict:
    w = ctx.world
    oa, ob = w.catalog.get(a.satellite_a), w.catalog.get(a.satellite_b)
    if oa.id == ob.id:
        raise CatalogError("satellite_a and satellite_b must differ")
    start = (a.window_start_min or 0) * 60
    end = (a.window_end_min * 60) if a.window_end_min else w.horizon_s
    if end - start < 60:
        raise CatalogError("window must be at least 1 minute long")
    ca = closest_approach(w.catalog, oa.id, ob.id, w.t0, start, min(end, 24 * 3600))
    tca = parse_iso(ca["tca"])
    ages = {o.id: round((tca - o.epoch).total_seconds() / 3600, 2) for o in (oa, ob)}
    unc = {k: _uncertainty_km(v) for k, v in ages.items()}
    combined = round(float(np.hypot(*unc.values())), 2)
    matching = [e["id"] for e in w.events if {e["primary_id"], e["secondary_id"]} == {oa.id, ob.id}
                and abs(e["tca_offset_s"] - ca["tca_offset_s"]) < 120]
    return {
        "objects": [_brief(oa), _brief(ob)],
        "window": [iso(add_s(w.t0, start)), iso(add_s(w.t0, end))],
        **ca,
        "matching_event_id": matching[0] if matching else None,
        "element_age_at_tca_hours": ages,
        "indicative_position_uncertainty_km": unc,
        "indicative_combined_uncertainty_km": combined,
        "miss_vs_uncertainty_ratio": round(ca["miss_km"] / combined, 3) if combined else None,
        "assessment_type": "deterministic miss-distance screening; collision probability NOT computed",
        "limitations": LIMITATIONS[:2],
    }


def simulate_maneuver(ctx: Context, a: ManeuverArgs) -> dict:
    w = ctx.world
    ev = w.event(a.event_id)
    if a.satellite_id not in (ev["primary_id"], ev["secondary_id"]):
        raise CatalogError(f"{a.satellite_id} is not part of event {a.event_id}")
    plan = ManeuverPlan(a.satellite_id, ev["tca_offset_s"] - a.burn_lead_time_min * 60,
                        (a.dv_radial_m_s, a.dv_along_track_m_s, a.dv_normal_m_s), a.label)
    res = _simulate(w.catalog, w.t0, w.now_offset_s, ev, plan, w.horizon_s, threshold_km=5.0,
                    baseline_events=w.events)
    ctx.db.save_maneuver(res, ctx.inv_id)
    return res


def compare_maneuvers(ctx: Context, a: CompareArgs) -> dict:
    known = {m["maneuver_id"]: m for m in ctx.db.maneuvers(inv_id=ctx.inv_id)}
    missing = [m for m in a.maneuver_ids if m not in known]
    if missing:
        raise CatalogError(f"unknown maneuver_ids {missing}; simulated so far: {list(known)}")
    out = _compare([known[m] for m in dict.fromkeys(a.maneuver_ids)], a.min_safe_miss_km)
    ctx.last_comparison = out
    return out


def generate_incident_report(ctx: Context, a: ReportArgs) -> dict:
    w = ctx.world
    ev = w.event(a.event_id)
    oa, ob = w.catalog.get(ev["primary_id"]), w.catalog.get(ev["secondary_id"])
    tca = parse_iso(ev["tca"])
    steps = [s for s in ctx.db.steps(ctx.inv_id) if s["kind"] == "tool"]
    sims = ctx.db.maneuvers(inv_id=ctx.inv_id)
    limitations = list(LIMITATIONS)
    if w.catalog.source == "simulated":
        limitations.insert(0, "ALL DATA IN THIS SCENARIO IS SIMULATED for demonstration purposes.")
    elif w.catalog.source == "celestrak-snapshot":
        limitations.insert(0, f"Live data unavailable: using bundled snapshot fetched {w.catalog.fetched_utc}.")
    report = {
        "report_id": "RPT-" + ev["id"][4:],
        "event": ev,
        "satellites": [
            {**_brief(o), "operator": o.operator, "maneuverable": o.maneuverable,
             "tle_epoch": iso(o.epoch), "element_age_at_tca_hours": round((tca - o.epoch).total_seconds() / 3600, 2),
             "freshness": element_freshness(o, tca)} for o in (oa, ob)],
        "data_source": w.provenance(),
        "analysis_performed": [{"evidence_ref": f"E{s['seq']}", "tool": s["name"], "status": s["status"]}
                               for s in steps],
        "maneuver_options": [{k: m[k] for k in ("maneuver_id", "label", "burn_time", "lead_time_before_tca_min",
                                                "dv_magnitude_m_s", "propellant_kg_est", "new_miss_km", "new_level",
                                                "delta_sma_km")}
                             | {"secondary_conjunctions": len(m["secondary_conjunctions"])} for m in sims],
        "comparison": ctx.last_comparison,
        "uncertainty_and_limitations": limitations,
        "human_approval": {"status": "pending", "note": "No command is generated or uplinked by this system."},
    }
    ctx.report = report
    ctx.db.update_investigation(ctx.inv_id, report=report)
    return {"report_id": report["report_id"], "sections": list(report), "options_included": len(sims),
            "comparison_included": ctx.last_comparison is not None,
            "next": "call submit_recommendation"}


# --- numeric grounding guardrail -----------------------------------------------

_STRIP = [re.compile(p) for p in (r"\d{4}-\d{2}-\d{2}[T ]?[\d:.]*Z?", r"\b\d{1,2}:\d{2}(:\d{2})?\b",
                                    r"\b[A-Z]{1,6}-[A-Z0-9-]+\b", r"\bE\d+\b", r"\bOGS-?\d\b")]
_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def _flatten_numbers(x: Any, out: list[float]) -> None:
    if isinstance(x, bool):
        return
    if isinstance(x, (int, float)):
        out.append(float(x))
    elif isinstance(x, dict):
        for v in x.values():
            _flatten_numbers(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _flatten_numbers(v, out)


def ungrounded_numbers(texts: list[str], evidence: list[Any]) -> list[str]:
    values: list[float] = []
    _flatten_numbers(evidence, values)
    vals = np.array(values) if values else np.zeros(0)
    bad = []
    for text in texts:
        for p in _STRIP:
            text = p.sub(" ", text)
        for m in _NUM.finditer(text):
            s = m.group()
            v = abs(float(s))
            if "." not in s and v <= 10:
                continue  # small counts like "3 options"
            decimals = len(s.split(".")[1]) if "." in s else 0
            half_ulp = 0.5 * 10.0 ** -decimals  # rounding the writer may have applied
            # unit conversions the model may reasonably apply: km<->m, m/s<->cm/s, s<->min<->h
            factors = np.array([1, 1e-3, 1e3, 1e-2, 1e2, 60, 1 / 60, 1 / 3600, 3600])
            cands = v * factors
            tol = np.maximum(half_ulp * factors, 0.01 * cands) + 1e-12
            if vals.size and (np.abs(np.abs(vals)[None, :] - cands[:, None]) <= tol[:, None]).any():
                continue
            bad.append(s)
    return sorted(set(bad))


def submit_recommendation(ctx: Context, a: RecommendationArgs) -> dict:
    if ctx.report is None:
        raise CatalogError("call generate_incident_report before submit_recommendation")
    known = {m["maneuver_id"]: m for m in ctx.db.maneuvers(inv_id=ctx.inv_id)}
    if a.decision == "maneuver":
        if not a.recommended_maneuver_id or a.recommended_maneuver_id not in known:
            raise CatalogError(f"decision 'maneuver' needs recommended_maneuver_id from {list(known)}")
        acceptable = (ctx.last_comparison or {}).get("ranked_acceptable", [])
        if a.recommended_maneuver_id not in acceptable:
            raise CatalogError(f"{a.recommended_maneuver_id} was not marked acceptable by the latest "
                               f"compare_maneuvers call (acceptable: {acceptable}). Simulate/compare more options "
                               f"or choose 'escalate'.")
    bad_refs = [r for r in a.evidence_refs if r not in ctx.evidence]
    if bad_refs:
        raise CatalogError(f"unknown evidence_refs {bad_refs}; valid ids: {sorted(ctx.evidence, key=lambda s: int(s[1:]))}")
    texts = [a.summary, *a.rationale, *a.risks_and_limitations, *a.next_steps]
    bad = ungrounded_numbers(texts, [ctx.event, list(ctx.evidence.values())])
    warning = None
    if bad:
        ctx.grounding_rejections += 1
        if ctx.grounding_rejections <= 2:
            raise CatalogError(f"GROUNDING CHECK FAILED: these numbers do not appear in any tool output: {bad}. "
                               f"Only quote values returned by tools (unit conversions are fine).")
        warning = f"Unverified numbers accepted after 2 rejections: {bad}"
    rec = a.model_dump()
    rec["grounding_check"] = "passed" if not bad else "warning"
    rec["grounding_warning"] = warning
    if a.recommended_maneuver_id:
        rec["maneuver"] = known.get(a.recommended_maneuver_id)
    ctx.recommendation = rec
    return {"accepted": True, "status": "awaiting_human_approval", "grounding_check": rec["grounding_check"]}


TOOLS: dict[str, tuple[type[BaseModel], Callable[[Context, Any], dict]]] = {
    "get_satellite_info": (SatInfoArgs, get_satellite_info),
    "get_trajectory": (TrajectoryArgs, get_trajectory),
    "analyze_close_approach": (CloseApproachArgs, analyze_close_approach),
    "simulate_maneuver": (ManeuverArgs, simulate_maneuver),
    "compare_maneuvers": (CompareArgs, compare_maneuvers),
    "generate_incident_report": (ReportArgs, generate_incident_report),
    "submit_recommendation": (RecommendationArgs, submit_recommendation),
}


def execute(ctx: Context, name: str, raw_args: str | dict) -> tuple[str, dict]:
    """Validate + run one tool with a timeout. Returns (evidence_ref, payload-for-the-model). Never raises."""
    start = time.perf_counter()
    args_obj: Any = raw_args
    status, result = "ok", None
    try:
        if name not in TOOLS:
            raise CatalogError(f"unknown tool '{name}'. Available: {list(TOOLS)}")
        if isinstance(raw_args, str):
            args_obj = json.loads(raw_args or "{}")
        model, fn = TOOLS[name]
        args = model.model_validate(args_obj)
        result = _pool.submit(fn, ctx, args).result(timeout=TOOL_TIMEOUT_S)
    except json.JSONDecodeError as e:
        status, result = "error", {"error": f"arguments are not valid JSON: {e}"}
    except ValidationError as e:
        status, result = "error", {"error": "invalid arguments",
                                   "details": [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5]]}
    except FutureTimeout:
        status, result = "error", {"error": f"tool timed out after {TOOL_TIMEOUT_S:.0f} s"}
    except (CatalogError, KeyError, ValueError, RuntimeError) as e:
        status, result = "error", {"error": str(e)}
    ms = int((time.perf_counter() - start) * 1000)
    seq = ctx.db.add_step(ctx.inv_id, "tool", name, args_obj, result, status, ms)
    ref = f"E{seq}"
    if status == "ok":
        ctx.evidence[ref] = result
    return ref, {"evidence_ref": ref, "ok": status == "ok", **({"result": result} if status == "ok" else result)}
