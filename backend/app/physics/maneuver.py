"""Simplified impulsive-maneuver model (DEMONSTRATION GRADE, NOT FLIGHT READY).

Method (differential correction):
  1. Take the SGP4 state of the primary at burn time t_b.
  2. Integrate two-body + J2 dynamics twice from t_b: once with the nominal velocity,
     once with velocity + delta-v.
  3. The difference of the two integrations is the maneuver-induced offset dr(t).
  4. Maneuvered position = SGP4 baseline(t) + dr(t) for t >= t_b.

This keeps the maneuvered trajectory consistent with the SGP4 screening baseline while
modelling the first-order effect of the burn. Drag differences, finite-burn effects,
attitude and execution errors are ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field

import numpy as np
from scipy.integrate import solve_ivp

from .constants import J2, MU_EARTH, R_EARTH
from .propagation import Epoch, TrackedObject, ric_frame, state
from .screening import (
    MAX_REL_SPEED_KM_S,
    Grid,
    coarse_minima,
    describe_approach,
    pair_dist_fn,
    refine_tca,
)

DIRECTIONS = ("prograde", "retrograde", "radial_out", "radial_in", "normal", "antinormal")
MAX_DV_M_S = 5.0

ASSUMPTIONS = [
    "Impulsive burn (instantaneous delta-v); finite-burn effects ignored.",
    "Maneuver effect modelled as two-body + J2 differential correction applied to the SGP4 baseline.",
    "Secondary object trajectory from SGP4 point estimate; no covariance, so no collision probability.",
    "Burn execution errors, attitude constraints and drag differences not modelled.",
    "Demonstration-grade result; NOT suitable for flight operations.",
]


class ManeuverError(ValueError):
    pass


def _accel(_t: float, y: np.ndarray) -> np.ndarray:
    r = y[:3]
    rn = np.linalg.norm(r)
    z2 = (r[2] / rn) ** 2
    k = -MU_EARTH / rn**3
    f = 1.5 * J2 * MU_EARTH * R_EARTH**2 / rn**5
    a = k * r + -f * np.array([r[0] * (1 - 5 * z2), r[1] * (1 - 5 * z2), r[2] * (3 - 5 * z2)])
    return np.concatenate([y[3:], a])


def _integrate(r0, v0, t0, t1):
    return solve_ivp(_accel, (t0, t1), np.concatenate([r0, v0]), method="DOP853", rtol=1e-11, atol=1e-11, dense_output=True)


def direction_vector(direction: str, r: np.ndarray, v: np.ndarray) -> np.ndarray:
    R, _, C = ric_frame(r, v)
    vh = v / np.linalg.norm(v)
    return {
        "prograde": vh,
        "retrograde": -vh,
        "radial_out": R,
        "radial_in": -R,
        "normal": C,
        "antinormal": -C,
    }[direction]


@dataclass
class ManeuverResult:
    satellite_id: str
    secondary_id: str
    direction: str
    delta_v_m_s: float
    burn_time_s: float
    burn_utc: str
    burn_lead_time_min: float
    baseline_tca_s: float
    baseline_miss_km: float
    new_tca_s: float
    new_tca_utc: str
    new_miss_km: float
    miss_change_km: float
    new_miss_ric_km: dict
    position_offset_at_tca_km: float
    secondary_conjunctions: list = field(default_factory=list)
    assumptions: list = field(default_factory=lambda: list(ASSUMPTIONS))
    model: str = "SGP4 baseline + two-body/J2 differential correction (impulsive)"

    def to_dict(self) -> dict:
        return asdict(self)


def make_offset_fn(primary: TrackedObject, epoch: Epoch, burn_time_s: float, dv_vec: np.ndarray, t_end: float):
    r0, v0 = state(primary.satrec, epoch, burn_time_s)
    nominal = _integrate(r0, v0, burn_time_s, t_end)
    burned = _integrate(r0, v0 + dv_vec, burn_time_s, t_end)
    if not (nominal.success and burned.success):
        raise ManeuverError("Numerical integration failed")

    def offset(t: float) -> np.ndarray:
        if t <= burn_time_s:
            return np.zeros(3)
        tt = min(t, t_end)
        return burned.sol(tt)[:3] - nominal.sol(tt)[:3]

    return offset


def simulate(
    primary: TrackedObject,
    secondary: TrackedObject,
    others: list[TrackedObject],
    epoch: Epoch,
    grid: Grid,
    baseline_tca_s: float,
    baseline_miss_km: float,
    burn_lead_time_min: float,
    direction: str,
    delta_v_m_s: float,
    report_km: float,
) -> ManeuverResult:
    if not primary.maneuverable:
        raise ManeuverError(f"{primary.id} is not maneuverable ({primary.kind}); choose the active spacecraft")
    if direction not in DIRECTIONS:
        raise ManeuverError(f"direction must be one of {DIRECTIONS}")
    if not (0.0 < delta_v_m_s <= MAX_DV_M_S):
        raise ManeuverError(f"delta_v_m_s must be in (0, {MAX_DV_M_S}]")
    burn_time = baseline_tca_s - burn_lead_time_min * 60.0
    if burn_lead_time_min < 1.0:
        raise ManeuverError("burn_lead_time_min must be >= 1 minute before TCA")
    if burn_time < 60.0:
        raise ManeuverError(
            f"burn would occur {burn_time/60:.1f} min after scenario epoch; max lead time is {(baseline_tca_s-60)/60:.1f} min"
        )

    t_end = float(grid.t[-1])
    r_b, v_b = state(primary.satrec, epoch, burn_time)
    dv_vec = direction_vector(direction, r_b, v_b) * (delta_v_m_s / 1000.0)
    offset = make_offset_fn(primary, epoch, burn_time, dv_vec, t_end)

    # New TCA with the original secondary (1 s scan, then bounded refinement).
    dist = pair_dist_fn(primary, secondary, epoch, offset)
    lo = max(burn_time, baseline_tca_s - 300.0)
    hi = min(t_end, baseline_tca_s + 300.0)
    scan = np.arange(lo, hi, 1.0)
    k = int(np.argmin([dist(x) for x in scan]))
    tca, miss = refine_tca(dist, max(lo, scan[k] - 1.0), min(hi, scan[k] + 1.0))

    def pos_a(t):
        r, v = state(primary.satrec, epoch, t)
        return r + offset(t), v

    desc = describe_approach(primary, secondary, epoch, tca, pos_a)

    # Re-screen maneuvered primary against the rest of the catalog (secondary conjunctions).
    ids = grid.ids
    ip = ids.index(primary.id)
    mask_t = grid.t >= burn_time
    tt = grid.t[mask_t]
    offs = np.array([offset(x) for x in tt])
    pp = grid.pos[ip][mask_t] + offs
    step = float(grid.t[1] - grid.t[0])
    coarse_km = report_km + 0.5 * step * MAX_REL_SPEED_KM_S
    new_conj = []
    for obj in others:
        if obj.id in (primary.id, secondary.id):
            continue
        j = ids.index(obj.id)
        d = np.linalg.norm(pp - grid.pos[j][mask_t], axis=1)
        fn = pair_dist_fn(primary, obj, epoch, offset)
        for kk in coarse_minima(d, coarse_km):
            t_lo, t_hi = max(tt[0], tt[kk] - step), min(tt[-1], tt[kk] + step)
            t_c, m = refine_tca(fn, t_lo, t_hi)
            if m <= report_km:
                a = describe_approach(primary, obj, epoch, t_c, pos_a)
                new_conj.append(a.to_dict())

    return ManeuverResult(
        satellite_id=primary.id,
        secondary_id=secondary.id,
        direction=direction,
        delta_v_m_s=round(delta_v_m_s, 4),
        burn_time_s=round(burn_time, 1),
        burn_utc=epoch.iso(burn_time),
        burn_lead_time_min=round(burn_lead_time_min, 2),
        baseline_tca_s=round(baseline_tca_s, 3),
        baseline_miss_km=round(baseline_miss_km, 4),
        new_tca_s=desc.tca_s,
        new_tca_utc=desc.tca_utc,
        new_miss_km=desc.miss_km,
        miss_change_km=round(desc.miss_km - baseline_miss_km, 4),
        new_miss_ric_km=desc.miss_ric_km,
        position_offset_at_tca_km=round(float(np.linalg.norm(offset(tca))), 4),
        secondary_conjunctions=new_conj,
    )
