"""Deterministic fallback investigator.

Used when the LLM is unavailable (no key, outage, refusal, timeout). It calls the *same* tools
with a fixed decision procedure, so the demo and the evidence trail still work. Clearly labelled.
"""
from __future__ import annotations

from .. import config
from .tools import InvestigationContext, run_tool

ACTOR = "fallback-planner"


def _call(ctx, name, inp):
    out, eid, err = run_tool(ctx, name, inp, ACTOR)
    return out, eid, err


def run_offline(ctx: InvestigationContext, reason: str) -> dict:
    ctx.log("system", output={"message": f"Deterministic fallback planner engaged: {reason}"})
    ev = ctx.event
    pid, sid = ev["primary_id"], ev["secondary_id"]
    pinfo, e_p, _ = _call(ctx, "get_satellite_info", {"satellite_id": pid})
    sinfo, e_s, _ = _call(ctx, "get_satellite_info", {"satellite_id": sid})
    ca, e_ca, _ = _call(ctx, "analyze_close_approach", {"satellite_a": pid, "satellite_b": sid})
    tca_min = ev["tca_s"] / 60
    horizon_min = ctx.mission.horizon_s / 60
    _call(ctx, "get_trajectory", {"satellite_id": pid, "start_min": max(0.0, tca_min - 20), "end_min": min(horizon_min, tca_min + 20), "step_s": 60})

    match = min(ca.get("approaches_within_50km", []) or [{}], key=lambda a: abs(a.get("tca_s", 1e9) - ev["tca_s"]))
    sigma = match.get("combined_position_sigma_km", 1.414)
    miss = ev["miss_km"]
    period = pinfo["elements"]["period_min"]
    rationale = [
        {"claim": f"Predicted miss distance {miss} km at TCA {ev['tca_utc']} with relative speed {ev['rel_speed_km_s']} km/s.", "evidence": [e_ca]},
        {"claim": f"Combined 1-sigma position uncertainty is about {sigma} km, so the miss is {match.get('miss_over_sigma')} sigma.", "evidence": [e_ca]},
        {"claim": f"{pinfo['name']} maneuverable={pinfo['maneuverable']}; {sinfo['name']} maneuverable={sinfo['maneuverable']}.", "evidence": [e_p, e_s]},
    ]
    uncertainties = [
        "No covariance data: result is screening-grade, not a probability of collision.",
        f"TLE ages at scenario epoch: {pinfo['tle_age_days_at_scenario_epoch']} d and {sinfo['tle_age_days_at_scenario_epoch']} d.",
        "Maneuver predictions use a simplified impulsive two-body/J2 differential model.",
    ]
    action, mid, risk = "NO_ACTION", "", "LOW"
    next_steps = ["Continue routine screening on the next catalog update."]

    if miss < config.AMBER_KM:
        risk = "HIGH" if miss < config.RED_KM else "ELEVATED"
        next_steps = ["Request updated tracking / ephemeris for both objects to reduce uncertainty.",
                      "Re-run screening when new orbital data arrives."]
    if miss < config.AMBER_KM and pinfo["maneuverable"]:
        target = round(max(config.DEFAULT_TARGET_MISS_KM, 2 * sigma), 1)
        leads = [x for x in (0.5 * period, 1.5 * period) if x <= tca_min - 1.5]
        if not leads:
            leads = [max(1.0, tca_min - 1.5)]
        options = [("prograde", leads[-1]), ("retrograde", leads[-1])] + ([("prograde", leads[0])] if len(leads) > 1 else [])
        dv = 0.1
        sims: list[tuple[str, dict]] = []
        for _round in range(3):
            for direction, lead in options:
                out, eid, err = _call(ctx, "simulate_maneuver", {"satellite_id": pid, "secondary_id": sid, "burn_lead_time_min": round(lead, 1), "direction": direction, "delta_v_m_s": dv})
                if not err:
                    sims.append((eid, out))
            if any(o["new_miss_km"] >= target and not o["secondary_conjunctions"] for _, o in sims):
                break
            dv = round(dv * 2, 3)
        if sims:
            cmp_out, e_cmp, _ = _call(ctx, "compare_maneuvers", {"maneuver_ids": [o["maneuver_id"] for _, o in sims], "target_miss_km": target})
            best = cmp_out.get("best_option")
            if best:
                b = next(o for _, o in sims if o["maneuver_id"] == best)
                e_b = next(e for e, o in sims if o["maneuver_id"] == best)
                rationale.append({"claim": f"Option {best} ({b['direction']} {b['delta_v_m_s']} m/s, {b['burn_lead_time_min']} min before TCA) raises predicted miss to {b['new_miss_km']} km with no new conjunctions found.", "evidence": [e_b, e_cmp]})
                if miss < config.RED_KM:
                    action, mid = "PREPARE_MANEUVER_FOR_APPROVAL", best
                    next_steps.insert(0, f"Operator review of {best}; confirm with refined orbit determination before any real-world planning.")
                else:
                    action = "MONITOR_AND_REQUEST_TRACKING"
                    next_steps.insert(0, f"Keep {best} as a contingency option if updated data shows the miss shrinking.")
            else:
                action = "MONITOR_AND_REQUEST_TRACKING"
                rationale.append({"claim": f"No simulated option met the {target} km target without new conjunctions.", "evidence": [e_cmp]})
    elif miss < config.AMBER_KM:
        action = "MONITOR_AND_REQUEST_TRACKING"
        rationale.append({"claim": "Neither object is known to be maneuverable, so only monitoring and coordination are possible.", "evidence": [e_p, e_s]})

    _call(ctx, "generate_incident_report", {"event_id": ev["id"]})
    headline = {
        "PREPARE_MANEUVER_FOR_APPROVAL": f"High-risk close approach: prepare {mid} for operator approval",
        "MONITOR_AND_REQUEST_TRACKING": "Elevated close approach: monitor and request updated tracking",
        "NO_ACTION": "Close approach outside action thresholds: no action required",
    }[action]
    return {"headline": headline, "risk_level": risk, "recommended_action": action, "recommended_maneuver_id": mid,
            "rationale": rationale, "uncertainties": uncertainties, "next_steps": next_steps}
