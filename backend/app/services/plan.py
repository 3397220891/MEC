"""Maneuver plan for the globe: every option the agent simulated, plus the maneuvered path of one.

The agent (LLM or fallback planner) already chose and simulated maneuvers with the physics tools; this
module only re-derives geometry for display, with the same model the tools used (SGP4 baseline +
two-body/J2 differential offset after an impulsive burn), so the drawn path matches the numbers in the report.
"""
from __future__ import annotations

import numpy as np

from .. import db
from ..physics import maneuver as mnv
from ..physics.propagation import state, states


class PlanError(ValueError):
    pass


def _fmt_label(o: dict) -> str:
    return f"T−{o['burn_lead_time_min']:g} min · {o['delta_v_m_s']:g} m/s {o['direction'].replace('_', ' ')}"


def build_plan(mission, inv: dict, maneuver_id: str | None = None, step_s: float = 60.0) -> dict:
    if inv["scenario_id"] != mission.scenario_id:
        raise PlanError(f"investigation {inv['id']} belongs to scenario '{inv['scenario_id']}', not the active '{mission.scenario_id}'")
    ev = next((e for e in mission.events if e["id"] == inv["event_id"]), None)
    if ev is None:
        raise PlanError(f"event {inv['event_id']} is not in the active scenario")
    primary = mission.obj(ev["primary_id"])

    comp = (inv.get("report") or {}).get("maneuver_comparison") or {}
    rows = {r["option_id"]: r for r in comp.get("options", [])}
    rec = inv.get("recommendation") or {}
    recommended = rec.get("recommended_maneuver_id") or comp.get("best_option") or ""

    options = []
    for r in db.query("SELECT data FROM maneuvers WHERE investigation_id = ? ORDER BY created_at", (inv["id"],)):
        mr = r["data"]
        if mr.get("satellite_id") != ev["primary_id"] or mr.get("secondary_id") != ev["secondary_id"]:
            continue  # trade studies against other objects are not part of this event's plan
        pos, _ = state(primary.satrec, mission.epoch, mr["burn_time_s"])
        row = rows.get(mr["maneuver_id"])
        new_conj = len(mr.get("secondary_conjunctions") or [])
        target = comp.get("target_miss_km")
        if row is not None:
            acceptable = bool(row.get("acceptable"))
        else:  # simulated after the last comparison: apply the same rule the comparison tool uses
            acceptable = target is not None and mr["new_miss_km"] >= target and new_conj == 0
        o = {
            "maneuver_id": mr["maneuver_id"],
            "direction": mr["direction"],
            "delta_v_m_s": mr["delta_v_m_s"],
            "burn_time_s": mr["burn_time_s"],
            "burn_utc": mr["burn_utc"],
            "burn_lead_time_min": mr["burn_lead_time_min"],
            "burn_position_km": np.round(pos, 2).tolist(),
            "new_tca_s": mr["new_tca_s"],
            "new_tca_utc": mr["new_tca_utc"],
            "new_miss_km": mr["new_miss_km"],
            "new_conjunctions": new_conj,
            "acceptable": acceptable,
            "in_comparison": row is not None,
            "recommended": mr["maneuver_id"] == recommended,
        }
        o["label"] = _fmt_label(o)
        options.append(o)

    chosen_id = maneuver_id or recommended or next((o["maneuver_id"] for o in options if o["acceptable"]), "")
    chosen = next((o for o in options if o["maneuver_id"] == chosen_id), None)
    if maneuver_id and chosen is None:
        raise PlanError(f"maneuver '{maneuver_id}' was not simulated for {ev['id']} in {inv['id']}")

    path = None
    if chosen:
        t = np.arange(0.0, mission.horizon_s + 1e-9, step_s)
        r, _, err = states(primary.satrec, mission.epoch, t)
        r0, v0 = state(primary.satrec, mission.epoch, chosen["burn_time_s"])
        dv = mnv.direction_vector(chosen["direction"], r0, v0) * (chosen["delta_v_m_s"] / 1000.0)
        offset = mnv.make_offset_fn(primary, mission.epoch, chosen["burn_time_s"], dv, mission.horizon_s)
        r = r + np.array([offset(float(x)) for x in t])
        r[err != 0] = np.nan
        new_tca_pos = np.asarray(state(primary.satrec, mission.epoch, chosen["new_tca_s"])[0]) + offset(chosen["new_tca_s"])
        path = {"maneuver_id": chosen["maneuver_id"], "satellite_id": primary.id, "step_s": step_s, "n": len(t),
                "positions": np.round(r, 2).flatten().tolist(), "new_tca_position_km": np.round(new_tca_pos, 2).tolist()}

    return {
        "investigation_id": inv["id"], "event_id": ev["id"], "primary_id": ev["primary_id"], "secondary_id": ev["secondary_id"],
        "tca_s": ev["tca_s"], "baseline_miss_km": ev["miss_km"], "target_miss_km": comp.get("target_miss_km"),
        "recommended_maneuver_id": recommended, "recommended_action": rec.get("recommended_action"),
        "decision": (inv.get("decision") or {}).get("decision"),
        "options": options, "path": path,
        "model": "SGP4 baseline + two-body/J2 differential correction (impulsive); advisory only, nothing is executed",
    }
