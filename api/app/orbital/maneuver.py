"""Hypothetical impulsive-maneuver simulation (SIMPLIFIED DEMONSTRATION MODEL).

Differential-correction approach
    r_man(t) = r_sgp4(t) + [r_J2(t | v_b + dv) - r_J2(t | v_b)]     for t >= t_burn
Both bracketed terms are integrated with the same two-body + J2 Cowell model from
the SGP4 state at burn time. Model errors common to both runs cancel, so the
difference isolates the effect of the burn, and the "no maneuver" option
reproduces the baseline SGP4 screening result exactly.

Not modelled: finite burn duration, attitude/pointing errors, thrust
uncertainty, drag change from the new orbit, state-estimate covariance.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
from scipy.integrate import solve_ivp

from .catalog import Catalog, CatalogError
from .screening import StateFn, describe_encounter, level_for, refine_tca, screen_pairs, sgp4_state_fn
from .timeutil import J2, MU, RE, add_s, iso, rtn_basis

MAX_DV_M_S = 2.0  # sanity limit for a single collision-avoidance burn in this demo
MIN_COMMAND_LEAD_S = 600.0  # burns must be at least 10 min after "now" (uplink lead time)
DEFAULT_ISP_S = 220.0  # monopropellant hydrazine assumption
G0 = 9.80665

ASSUMPTIONS = [
    "Impulsive burn (instantaneous delta-v), no execution error.",
    "Burn effect propagated with two-body + J2 and added to the SGP4 nominal (differential correction).",
    "Other objects follow their unmodified SGP4 trajectories.",
    "No covariance data: results are deterministic miss distances, not collision probabilities.",
    "Propellant mass estimated with the rocket equation using assumed Isp and spacecraft mass.",
]


def _rhs(_t, y):
    r = y[:3]
    rn = np.linalg.norm(r)
    z2 = (r[2] / rn) ** 2
    k = 1.5 * J2 * MU * RE**2 / rn**5
    a = -MU * r / rn**3
    a = a + k * r * np.array([5 * z2 - 1, 5 * z2 - 1, 5 * z2 - 3])
    return np.concatenate([y[3:], a])


def _integrate(r0, v0, t_start, t_end):
    return solve_ivp(_rhs, (t_start, t_end), np.concatenate([r0, v0]), method="DOP853",
                     rtol=1e-11, atol=1e-9, dense_output=True)


@dataclass
class ManeuverPlan:
    satellite_id: str
    burn_offset_s: float  # seconds after scenario t0
    dv_rtn_m_s: tuple[float, float, float]  # radial, along-track (prograde +), normal
    label: str = ""

    @property
    def dv_m_s(self) -> float:
        return float(np.linalg.norm(self.dv_rtn_m_s))

    def key(self) -> str:
        raw = json.dumps([self.satellite_id, round(self.burn_offset_s, 1), [round(x, 5) for x in self.dv_rtn_m_s]])
        return "MNV-" + hashlib.sha1(raw.encode()).hexdigest()[:8].upper()


class ManeuveredTrajectory:
    def __init__(self, catalog: Catalog, t0: datetime, plan: ManeuverPlan, end_offset_s: float):
        obj = catalog.get(plan.satellite_id)
        self.base: StateFn = sgp4_state_fn(obj, t0)
        self.tb = plan.burn_offset_s
        self.end = end_offset_s
        r_b, v_b = self.base(self.tb)
        self.r_burn, self.v_burn = r_b, v_b
        self.dv_eci = rtn_basis(r_b, v_b).T @ (np.asarray(plan.dv_rtn_m_s, dtype=float) / 1000.0)
        self.zero = not np.any(self.dv_eci)
        if not self.zero:
            self.ref = _integrate(r_b, v_b, self.tb, end_offset_s)
            self.man = _integrate(r_b, v_b + self.dv_eci, self.tb, end_offset_s)

    def delta(self, t: np.ndarray) -> np.ndarray:
        t = np.atleast_1d(np.asarray(t, dtype=float))
        out = np.zeros((len(t), 6))
        if self.zero:
            return out
        m = t > self.tb
        if m.any():
            tt = np.clip(t[m], self.tb, self.end)
            out[m] = (self.man.sol(tt) - self.ref.sol(tt)).T
        return out

    def state(self, t_off: float):
        r, v = self.base(t_off)
        d = self.delta(np.array([t_off]))[0]
        return r + d[:3], v + d[3:]


def _sma(r, v) -> float:
    return 1.0 / (2.0 / np.linalg.norm(r) - np.dot(v, v) / MU)


def validate_plan(catalog: Catalog, plan: ManeuverPlan, now_offset_s: float, tca_offset_s: float) -> None:
    obj = catalog.get(plan.satellite_id)
    if not obj.maneuverable:
        raise CatalogError(f"{obj.id} ({obj.name}) is not flagged as maneuverable by its operator")
    if not all(math.isfinite(x) for x in plan.dv_rtn_m_s):
        raise CatalogError("delta-v components must be finite numbers")
    if plan.dv_m_s > MAX_DV_M_S:
        raise CatalogError(f"|dv| = {plan.dv_m_s:.3f} m/s exceeds the {MAX_DV_M_S} m/s demo limit")
    if plan.burn_offset_s < now_offset_s + MIN_COMMAND_LEAD_S:
        raise CatalogError(f"burn must be at least {MIN_COMMAND_LEAD_S / 60:.0f} min after the current "
                           f"simulation time (earliest allowed offset: {now_offset_s + MIN_COMMAND_LEAD_S:.0f} s)")
    if plan.burn_offset_s > tca_offset_s - 60:
        raise CatalogError("burn must occur at least 60 s before the time of closest approach")


def simulate_maneuver(
    catalog: Catalog,
    t0: datetime,
    now_offset_s: float,
    event: dict,
    plan: ManeuverPlan,
    horizon_s: float,
    threshold_km: float = 5.0,
    baseline_events: Optional[list[dict]] = None,
    isp_s: float = DEFAULT_ISP_S,
) -> dict:
    """Simulate one hypothetical maneuver against one conjunction event + re-screen against all objects."""
    tca = float(event["tca_offset_s"])
    validate_plan(catalog, plan, now_offset_s, tca)
    sat = catalog.get(plan.satellite_id)
    other_id = event["secondary_id"] if event["primary_id"] == plan.satellite_id else event["primary_id"]
    end = max(horizon_s, tca + 1800)
    traj = ManeuveredTrajectory(catalog, t0, plan, end)

    # 1) new closest approach with the threat object (search +-20 min around the original TCA)
    f_other = sgp4_state_fn(catalog.get(other_id), t0)
    grid = np.arange(max(plan.burn_offset_s, tca - 1200), tca + 1200 + 1e-9, 2.0)
    _, r_o, _, _ = catalog.propagate(t0, grid, [other_id])
    _, r_s, _, _ = catalog.propagate(t0, grid, [plan.satellite_id])
    r_m = r_s[0] + traj.delta(grid)[:, :3]
    k = int(np.argmin(np.linalg.norm(r_m - r_o[0], axis=-1)))
    t_new = refine_tca(traj.state, f_other, grid[max(k - 1, 0)], grid[min(k + 1, len(grid) - 1)])
    enc = describe_encounter(traj.state, f_other, t_new)

    # 2) secondary screening: maneuvered trajectory vs every other object until end of horizon
    step = 10.0
    offs = np.arange(plan.burn_offset_s, horizon_s + 1e-9, step)
    others = [i for i in catalog.ids() if i != plan.satellite_id]
    ids, r_all, v_all, err = catalog.propagate(t0, offs, [plan.satellite_id] + others)
    d = traj.delta(offs)
    pos = {i: np.where(err[n][:, None] == 0, r_all[n], np.nan) for n, i in enumerate(ids)}
    vel = {i: v_all[n] for n, i in enumerate(ids)}
    pos[plan.satellite_id] = pos[plan.satellite_id] + d[:, :3]
    vel[plan.satellite_id] = vel[plan.satellite_id] + d[:, 3:]
    fns = {i: sgp4_state_fn(catalog.objects[i], t0) for i in others}
    fns[plan.satellite_id] = traj.state
    found = screen_pairs(t0, offs, pos, vel, fns, [(plan.satellite_id, o) for o in others], threshold_km)
    base_by_obj = {}
    for e in baseline_events or []:
        if plan.satellite_id in (e["primary_id"], e["secondary_id"]):
            o = e["secondary_id"] if e["primary_id"] == plan.satellite_id else e["primary_id"]
            base_by_obj[o] = min(base_by_obj.get(o, math.inf), e["miss_km"])
    secondary = []
    for c in found:
        if c.secondary_id == other_id and abs(c.tca_offset_s - t_new) < 600:
            continue  # this is the encounter we are trying to fix
        base = base_by_obj.get(c.secondary_id)
        if base is None:
            status = "NEW"
        elif c.miss_km < base - 0.01:
            status = "worsened"
        elif c.miss_km > base + 0.01:
            status = "improved"
        else:
            status = "unchanged"
        secondary.append({
            "object_id": c.secondary_id, "object_name": catalog.objects[c.secondary_id].name,
            "tca": c.tca, "miss_km": c.miss_km, "level": c.level, "baseline_miss_km": base, "status": status,
        })

    mass = sat.mass_kg or 500.0
    prop = mass * (1 - math.exp(-plan.dv_m_s / (isp_s * G0)))
    r_b, v_b = traj.r_burn, traj.v_burn
    d_sma = _sma(r_b, v_b + traj.dv_eci) - _sma(r_b, v_b)
    return {
        "maneuver_id": plan.key(),
        "label": plan.label or describe_plan(plan),
        "event_id": event["id"],
        "satellite_id": plan.satellite_id,
        "threat_object_id": other_id,
        "burn_time": iso(add_s(t0, plan.burn_offset_s)),
        "burn_offset_s": round(plan.burn_offset_s, 1),
        "lead_time_before_tca_min": round((tca - plan.burn_offset_s) / 60, 2),
        "dv_rtn_m_s": [round(x, 5) for x in plan.dv_rtn_m_s],
        "dv_magnitude_m_s": round(plan.dv_m_s, 5),
        "propellant_kg_est": round(prop, 4),
        "baseline_miss_km": event["miss_km"],
        "new_tca": iso(add_s(t0, t_new)),
        "new_tca_shift_s": round(t_new - tca, 3),
        "new_miss_km": round(enc["miss_km"], 4),
        "new_level": level_for(enc["miss_km"]),
        "miss_change_km": round(enc["miss_km"] - event["miss_km"], 4),
        "new_rtn_miss_km": [round(enc["radial_km"], 4), round(enc["intrack_km"], 4), round(enc["crosstrack_km"], 4)],
        "rel_speed_km_s": round(enc["rel_speed_km_s"], 4),
        "delta_sma_km": round(d_sma, 4),
        "secondary_conjunctions": secondary,
        "secondary_screening": {"objects_checked": len(others), "threshold_km": threshold_km,
                                "window": [iso(add_s(t0, plan.burn_offset_s)), iso(add_s(t0, horizon_s))]},
        "model": "SGP4 nominal + two-body/J2 differential correction (simplified demonstration)",
        "assumptions": ASSUMPTIONS + [f"Spacecraft mass {mass:.0f} kg, Isp {isp_s:.0f} s."],
    }


def describe_plan(plan: ManeuverPlan) -> str:
    if plan.dv_m_s == 0:
        return "No maneuver (baseline)"
    r, t, n = plan.dv_rtn_m_s
    comp = max(((abs(r), "radial" + ("+" if r > 0 else "-")), (abs(t), "prograde" if t > 0 else "retrograde"),
                (abs(n), "normal" + ("+" if n > 0 else "-"))))[1]
    return f"{comp} {plan.dv_m_s * 100:.1f} cm/s"


def compare_maneuvers(results: list[dict], min_safe_miss_km: float = 1.0) -> dict:
    """Deterministic ranking: must clear the threat AND create no RED/AMBER secondary events; then lowest dv,
    then latest burn (more time for updated tracking data before committing)."""
    rows, accepted = [], []
    for r in results:
        reasons = []
        if r["new_miss_km"] < min_safe_miss_km:
            reasons.append(f"miss {r['new_miss_km']:.3f} km < required {min_safe_miss_km} km")
        bad = [s for s in r["secondary_conjunctions"]
               if s["level"] in ("RED", "AMBER") and s["status"] in ("NEW", "worsened")]
        if bad:
            reasons.append("creates/worsens conjunction with " + ", ".join(
                f"{s['object_name']} ({s['miss_km']:.2f} km)" for s in bad))
        row = {k: r[k] for k in ("maneuver_id", "label", "dv_magnitude_m_s", "propellant_kg_est", "burn_time",
                                 "lead_time_before_tca_min", "new_miss_km", "miss_change_km", "new_level")}
        row["secondary_issues"] = len(bad)
        row["meets_criteria"] = not reasons
        row["rejection_reasons"] = reasons
        rows.append(row)
        if not reasons:
            accepted.append(row)
    accepted.sort(key=lambda x: (round(x["dv_magnitude_m_s"], 4), x["lead_time_before_tca_min"]))
    best = accepted[0] if accepted else None
    return {
        "criteria": {
            "min_safe_miss_km": min_safe_miss_km,
            "no_new_or_worsened_red_amber_secondary": True,
            "ranking": "lowest delta-v, then shortest lead time (latest burn keeps options open)",
        },
        "options": rows,
        "ranked_acceptable": [a["maneuver_id"] for a in accepted],
        "best_option": best["maneuver_id"] if best else None,
        "note": None if best else "No simulated option meets the criteria; simulate additional options or escalate.",
    }
