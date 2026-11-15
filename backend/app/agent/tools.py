"""Agent tools: JSON schemas + deterministic executors.

Every successful tool call is registered as evidence (E1, E2, ...). The agent may only cite
evidence IDs that exist; the report validator enforces this.
No tool can command a spacecraft: tools are read-only analysis or offline simulation.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .. import config, db
from ..physics import maneuver as mnv
from ..physics.constants import R_EARTH
from ..physics.propagation import states
from ..physics.screening import screen_pair
from ..services.state import Mission, ground_track


class ToolError(Exception):
    pass


def _strict(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required if required is not None else list(properties),
            "additionalProperties": False,
        },
    }


TOOL_SCHEMAS = [
    _strict(
        "get_satellite_info",
        "Retrieve metadata and current orbital parameters for one tracked object: kind, operator, whether it can "
        "maneuver, data source (SIMULATED or REAL), TLE epoch/age, altitude, speed and osculating elements at the "
        "scenario epoch. Use this before reasoning about who can act and how fresh the data is.",
        {"satellite_id": {"type": "string", "description": "Object id, e.g. 'SIM-90003' or 'NORAD-25544'."}},
    ),
    _strict(
        "get_trajectory",
        "Retrieve predicted SGP4 trajectory samples for one object over a time window measured in minutes from the "
        "scenario epoch (0 .. horizon). step_s must be between 10 and 600. Returns summary statistics plus up to 24 "
        "down-sampled points (altitude, speed, approximate lat/lon).",
        {
            "satellite_id": {"type": "string"},
            "start_min": {"type": "number", "description": "Window start, minutes after scenario epoch."},
            "end_min": {"type": "number", "description": "Window end, minutes after scenario epoch."},
            "step_s": {"type": "number", "description": "Sampling step in seconds (10-600)."},
        },
    ),
    _strict(
        "analyze_close_approach",
        "Deterministically screen one pair of objects over the full horizon: finds every local close approach "
        "within 50 km, refines time of closest approach (TCA) and miss distance, returns relative speed, "
        "radial/in-track/cross-track miss components and an estimated position-uncertainty budget from TLE age. "
        "This is screening, NOT a collision probability.",
        {"satellite_a": {"type": "string"}, "satellite_b": {"type": "string"}},
    ),
    _strict(
        "simulate_maneuver",
        "Simulate a HYPOTHETICAL impulsive maneuver of a maneuverable satellite and recompute its closest approach "
        "to the secondary object, then re-screen the maneuvered orbit against every other catalog object to detect "
        "new (secondary) conjunctions. burn_lead_time_min = minutes before the baseline TCA (>= 1). "
        "delta_v_m_s must be in (0, 5]. Model: SGP4 baseline + two-body/J2 differential correction. "
        "Demonstration grade only; nothing is sent to any spacecraft.",
        {
            "satellite_id": {"type": "string", "description": "The maneuvering (active) satellite."},
            "secondary_id": {"type": "string", "description": "The object to avoid."},
            "burn_lead_time_min": {"type": "number"},
            "direction": {"type": "string", "enum": list(mnv.DIRECTIONS)},
            "delta_v_m_s": {"type": "number"},
        },
    ),
    _strict(
        "compare_maneuvers",
        "Compare previously simulated maneuvers (by maneuver_id, e.g. 'M1') against the no-maneuver baseline. "
        "Ranks options: must meet target_miss_km and create no new conjunctions; then lowest delta-v; then larger "
        "miss. Returns a table with assumptions.",
        {
            "maneuver_ids": {"type": "array", "items": {"type": "string"}},
            "target_miss_km": {"type": "number", "description": "Minimum acceptable predicted miss distance (km)."},
        },
    ),
    _strict(
        "generate_incident_report",
        "Compile the deterministic, evidence-backed incident report for the event from all tool results gathered "
        "in this investigation (event summary, objects, data freshness, analyses performed, maneuver comparison, "
        "limitations). Call this once, after analysis is complete and before giving your final recommendation.",
        {"event_id": {"type": "string"}},
    ),
]

TOOL_NAMES = [t["name"] for t in TOOL_SCHEMAS]


@dataclass
class InvestigationContext:
    id: str
    mission: Mission
    event: dict
    evidence: dict = field(default_factory=dict)  # E# -> {tool, input, output}
    maneuvers: dict = field(default_factory=dict)  # M# -> result dict
    comparison: dict | None = None
    report: dict | None = None
    step_idx: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def next_idx(self) -> int:
        with self._lock:
            self.step_idx += 1
            return self.step_idx

    def log(self, kind: str, tool: str | None = None, input: Any = None, output: Any = None,
            status: str = "ok", duration_ms: float = 0.0, evidence_id: str | None = None) -> None:
        db.execute(
            "INSERT INTO steps (investigation_id, idx, kind, tool, input, output, evidence_id, status, duration_ms, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (self.id, self.next_idx(), kind, tool, db.dumps(input), db.dumps(output), evidence_id, status, duration_ms, db.now_iso()),
        )

    def add_evidence(self, tool: str, inp: dict, out: dict) -> str:
        with self._lock:
            eid = f"E{len(self.evidence) + 1}"
            self.evidence[eid] = {"tool": tool, "input": inp, "output": out}
            return eid


# ---------------------------------------------------------------- executors

def _num(inp: dict, key: str) -> float:
    try:
        v = float(inp[key])
    except (KeyError, TypeError, ValueError):
        raise ToolError(f"'{key}' must be a number")
    if not np.isfinite(v):
        raise ToolError(f"'{key}' must be finite")
    return v


def _obj(ctx: InvestigationContext, oid: Any):
    try:
        return ctx.mission.obj(str(oid))
    except KeyError as e:
        ids = ", ".join(o.id for o in ctx.mission.objects[:40])
        raise ToolError(f"{e.args[0]}. Known ids include: {ids}")


def t_get_satellite_info(ctx: InvestigationContext, inp: dict) -> dict:
    o = _obj(ctx, inp.get("satellite_id"))
    out = ctx.mission.object_summary(o)
    out["position_uncertainty_1sigma_km_at_event_tca"] = round(ctx.mission.position_sigma_km(o, ctx.event["tca_s"]), 3)
    return out


def t_get_trajectory(ctx: InvestigationContext, inp: dict) -> dict:
    o = _obj(ctx, inp.get("satellite_id"))
    m = ctx.mission
    s, e, step = _num(inp, "start_min") * 60, _num(inp, "end_min") * 60, _num(inp, "step_s")
    if not (0 <= s < e <= m.horizon_s):
        raise ToolError(f"window must satisfy 0 <= start_min < end_min <= {m.horizon_s/60:.0f}")
    if not (10 <= step <= 600):
        raise ToolError("step_s must be within 10..600")
    t = np.arange(s, e + 1e-9, step)
    if len(t) > 2000:
        raise ToolError("too many samples; increase step_s or shrink the window")
    r, v, err = states(o.satrec, m.epoch, t)
    alt = np.linalg.norm(r, axis=1) - R_EARTH
    spd = np.linalg.norm(v, axis=1)
    pick = np.unique(np.linspace(0, len(t) - 1, min(24, len(t))).astype(int))
    samples = []
    for k in pick:
        lat, lon = ground_track(r[k], m.epoch.gmst(t[k]))
        samples.append({"t_min": round(t[k] / 60, 2), "utc": m.epoch.iso(t[k]), "alt_km": round(float(alt[k]), 2),
                        "speed_km_s": round(float(spd[k]), 4), "lat_deg": round(lat, 2), "lon_deg": round(lon, 2)})
    return {
        "satellite_id": o.id,
        "frame": "TEME (positions), spherical lat/lon approximation",
        "n_samples": int(len(t)),
        "propagation_errors": int(np.count_nonzero(err)),
        "altitude_km": {"min": round(float(alt.min()), 2), "max": round(float(alt.max()), 2), "mean": round(float(alt.mean()), 2)},
        "speed_km_s": {"min": round(float(spd.min()), 4), "max": round(float(spd.max()), 4)},
        "samples": samples,
    }


def t_analyze_close_approach(ctx: InvestigationContext, inp: dict) -> dict:
    m = ctx.mission
    a, b = _obj(ctx, inp.get("satellite_a")), _obj(ctx, inp.get("satellite_b"))
    if a.id == b.id:
        raise ToolError("satellite_a and satellite_b must differ")
    ia, ib = m.grid.ids.index(a.id), m.grid.ids.index(b.id)
    found = screen_pair(a, b, m.epoch, m.grid.t, m.grid.pos[ia], m.grid.pos[ib], report_km=50.0)
    approaches = []
    for ap in found:
        sa, sb = m.position_sigma_km(a, ap.tca_s), m.position_sigma_km(b, ap.tca_s)
        comb = float(np.hypot(sa, sb))
        d = ap.to_dict()
        d.update({
            "tca_from_epoch_min": round(ap.tca_s / 60, 2),
            "combined_position_sigma_km": round(comb, 3),
            "miss_over_sigma": round(ap.miss_km / comb, 3),
            "inside_screening_volume": ap.miss_km <= config.SCREEN_THRESHOLD_KM,
            "severity": config.severity(ap.miss_km),
        })
        approaches.append(d)
    closest = min(approaches, key=lambda x: x["miss_km"]) if approaches else None
    return {
        "pair": [a.id, b.id],
        "horizon_min": round(m.horizon_s / 60, 1),
        "approaches_within_50km": approaches,
        "closest": closest,
        "method": "SGP4 30 s grid + bounded scalar TCA refinement (1 ms tolerance)",
        "uncertainty_model": "1-sigma position ~ 1 km at TLE epoch + 1.5 km/day of TLE age (order-of-magnitude, no covariance)",
        "note": "Screening result. No covariance is available, so no probability of collision is computed.",
    }


def _baseline_for(ctx: InvestigationContext, a, b) -> tuple[float, float]:
    ev = ctx.event
    if {a.id, b.id} == {ev["primary_id"], ev["secondary_id"]}:
        return ev["tca_s"], ev["miss_km"]
    m = ctx.mission
    found = screen_pair(a, b, m.epoch, m.grid.t, m.grid.pos[m.grid.ids.index(a.id)], m.grid.pos[m.grid.ids.index(b.id)], 50.0)
    if not found:
        raise ToolError("no close approach (<50 km) between these objects in the horizon; nothing to avoid")
    best = min(found, key=lambda x: x.miss_km)
    return best.tca_s, best.miss_km


def t_simulate_maneuver(ctx: InvestigationContext, inp: dict) -> dict:
    m = ctx.mission
    a, b = _obj(ctx, inp.get("satellite_id")), _obj(ctx, inp.get("secondary_id"))
    tca, miss = _baseline_for(ctx, a, b)
    try:
        res = mnv.simulate(a, b, m.objects, m.epoch, m.grid, tca, miss, _num(inp, "burn_lead_time_min"),
                           str(inp.get("direction")), _num(inp, "delta_v_m_s"), config.SCREEN_THRESHOLD_KM)
    except mnv.ManeuverError as e:
        raise ToolError(str(e))
    out = res.to_dict()
    with ctx._lock:
        mid = f"M{len(ctx.maneuvers) + 1}"
        out = {"maneuver_id": mid, **out}
        ctx.maneuvers[mid] = out
    db.execute("INSERT OR REPLACE INTO maneuvers VALUES (?,?,?,?)", (mid, ctx.id, db.dumps(out), db.now_iso()))
    return out


def compare(ctx: InvestigationContext, ids: list[str], target_km: float) -> dict:
    ev = ctx.event
    rows = [{
        "option_id": "NO_MANEUVER", "direction": None, "delta_v_m_s": 0.0, "burn_lead_time_min": None,
        "predicted_miss_km": ev["miss_km"], "tca_utc": ev["tca_utc"], "new_conjunctions": 0,
        "meets_target": ev["miss_km"] >= target_km,
    }]
    for mid in ids:
        r = ctx.maneuvers.get(mid)
        if r is None:
            raise ToolError(f"unknown maneuver_id '{mid}'. Simulated so far: {list(ctx.maneuvers) or 'none'}")
        rows.append({
            "option_id": mid, "direction": r["direction"], "delta_v_m_s": r["delta_v_m_s"],
            "burn_lead_time_min": r["burn_lead_time_min"], "predicted_miss_km": r["new_miss_km"],
            "tca_utc": r["new_tca_utc"], "new_conjunctions": len(r["secondary_conjunctions"]),
            "meets_target": r["new_miss_km"] >= target_km,
        })
    feasible = [x for x in rows if x["meets_target"] and x["new_conjunctions"] == 0]
    feasible.sort(key=lambda x: (x["delta_v_m_s"], -x["predicted_miss_km"]))
    for x in rows:
        x["acceptable"] = x in feasible
        x["miss_per_dv_km_per_m_s"] = round(x["predicted_miss_km"] / x["delta_v_m_s"], 3) if x["delta_v_m_s"] else None
    best = feasible[0]["option_id"] if feasible else None
    out = {
        "target_miss_km": target_km,
        "baseline_miss_km": ev["miss_km"],
        "options": rows,
        "best_option": best,
        "ranking_rule": "acceptable = meets target and no new conjunctions; rank by lowest delta-v, then largest miss",
        "assumptions": mnv.ASSUMPTIONS,
    }
    ctx.comparison = out
    return out


def t_compare_maneuvers(ctx: InvestigationContext, inp: dict) -> dict:
    ids = inp.get("maneuver_ids")
    if not isinstance(ids, list) or not ids:
        raise ToolError("maneuver_ids must be a non-empty list, e.g. ['M1','M2']")
    target = _num(inp, "target_miss_km")
    if not (0 < target <= 50):
        raise ToolError("target_miss_km must be within (0, 50]")
    return compare(ctx, [str(x) for x in ids], target)


def t_generate_incident_report(ctx: InvestigationContext, inp: dict) -> dict:
    from .report import build_report  # local import avoids a cycle

    if str(inp.get("event_id")) != ctx.event["id"]:
        raise ToolError(f"this investigation covers event '{ctx.event['id']}'")
    ctx.report = build_report(ctx)
    r = ctx.report
    return {"report_id": r["report_id"], "sections": list(r.keys()), "event_summary": r["event_summary"],
            "maneuver_options": len(r["maneuver_comparison"]["options"]) if r["maneuver_comparison"] else 0,
            "evidence_count": len(ctx.evidence)}


EXECUTORS: dict[str, Callable[[InvestigationContext, dict], dict]] = {
    "get_satellite_info": t_get_satellite_info,
    "get_trajectory": t_get_trajectory,
    "analyze_close_approach": t_analyze_close_approach,
    "simulate_maneuver": t_simulate_maneuver,
    "compare_maneuvers": t_compare_maneuvers,
    "generate_incident_report": t_generate_incident_report,
}


def run_tool(ctx: InvestigationContext, name: str, inp: dict, actor: str) -> tuple[dict, str | None, bool]:
    """Execute + log a tool call. Returns (payload, evidence_id, is_error)."""
    t0 = time.perf_counter()
    fn = EXECUTORS.get(name)
    try:
        if fn is None:
            raise ToolError(f"unknown tool '{name}'")
        if not isinstance(inp, dict):
            raise ToolError("tool input must be a JSON object")
        out = fn(ctx, inp)
        eid = ctx.add_evidence(name, inp, out)
        payload = {"evidence_id": eid, **out}
        ctx.log("tool", name, {"actor": actor, **inp}, payload, "ok", (time.perf_counter() - t0) * 1000, eid)
        return payload, eid, False
    except ToolError as e:
        payload = {"error": str(e)}
    except Exception as e:  # never let a tool crash the agent loop
        payload = {"error": f"internal tool failure: {type(e).__name__}: {e}"}
    ctx.log("tool", name, {"actor": actor, **(inp if isinstance(inp, dict) else {})}, payload, "error", (time.perf_counter() - t0) * 1000)
    return payload, None, True
