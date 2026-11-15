"""Close-approach screening.

Method
1. Propagate every object with vectorised SGP4 on a fixed time grid.
2. For each pair find local minima of the sampled separation.
3. Gate: with straight-line relative motion the sampled minimum is at most
   sqrt(miss^2 + (v_rel*dt/2)^2), so any pair whose sampled minimum is below
   sqrt(thr^2 + (v_rel*dt/2)^2) may truly come within thr. No events are missed
   by the coarse grid under that assumption.
4. Refine time of closest approach (TCA) with a bounded Brent search on exact
   SGP4 states, then express the miss vector in the primary's RTN frame.

This is *screening*, not a probability-of-collision (Pc) assessment: TLEs carry
no covariance, so Pc cannot be computed honestly from this data.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable, Iterable, Optional

import numpy as np
from scipy.optimize import minimize_scalar

from .catalog import Catalog, SpaceObject
from .timeutil import add_s, iso, rtn_basis, to_jd

RED_KM = 1.0
AMBER_KM = 5.0

StateFn = Callable[[float], tuple[np.ndarray, np.ndarray]]  # offset_s -> (r, v)


@dataclass
class Conjunction:
    id: str
    primary_id: str
    secondary_id: str
    tca: str
    tca_offset_s: float
    miss_km: float
    rel_speed_km_s: float
    radial_km: float
    intrack_km: float
    crosstrack_km: float
    level: str
    method: str = "SGP4 grid screening + Brent TCA refinement"

    def to_dict(self) -> dict:
        return asdict(self)


def level_for(miss_km: float) -> str:
    if miss_km < RED_KM:
        return "RED"
    if miss_km < AMBER_KM:
        return "AMBER"
    return "WATCH"


def event_id(a: str, b: str, tca_offset_s: float, t0: datetime) -> str:
    key = f"{a}|{b}|{iso(add_s(t0, round(tca_offset_s)))}"
    return "EVT-" + hashlib.sha1(key.encode()).hexdigest()[:8].upper()


def sgp4_state_fn(obj: SpaceObject, t0: datetime) -> StateFn:
    jd, fr = to_jd(t0)

    def f(offset_s: float):
        err, r, v = obj.satrec.sgp4(jd, fr + offset_s / 86400.0)
        if err:
            raise ValueError(f"SGP4 error {err} for {obj.id}")
        return np.array(r), np.array(v)

    return f


def refine_tca(fa: StateFn, fb: StateFn, lo: float, hi: float) -> float:
    res = minimize_scalar(lambda t: float(np.linalg.norm(fa(t)[0] - fb(t)[0])), bounds=(lo, hi),
                          method="bounded", options={"xatol": 1e-3})
    return float(res.x)


def describe_encounter(fa: StateFn, fb: StateFn, t_off: float) -> dict:
    ra, va = fa(t_off)
    rb, vb = fb(t_off)
    d = rb - ra
    rtn = rtn_basis(ra, va) @ d
    return {
        "miss_km": float(np.linalg.norm(d)),
        "rel_speed_km_s": float(np.linalg.norm(vb - va)),
        "radial_km": float(rtn[0]),
        "intrack_km": float(rtn[1]),
        "crosstrack_km": float(rtn[2]),
    }


def _candidate_minima(dist: np.ndarray, vrel: np.ndarray, step_s: float, thr_km: float) -> list[int]:
    """Indices k of sampled local minima that pass the conservative gate."""
    n = len(dist)
    if n < 2:
        return []
    left = np.r_[np.inf, dist[:-1]]
    right = np.r_[dist[1:], np.inf]
    is_min = (dist <= left) & (dist < right)
    gate = np.sqrt(thr_km**2 + (vrel * step_s / 2) ** 2) * 1.05
    return list(np.nonzero(is_min & (dist < gate))[0])


def screen_pairs(
    t0: datetime,
    offsets_s: np.ndarray,
    positions: dict[str, np.ndarray],
    velocities: dict[str, np.ndarray],
    state_fns: dict[str, StateFn],
    pairs: Iterable[tuple[str, str]],
    threshold_km: float,
) -> list[Conjunction]:
    step = float(offsets_s[1] - offsets_s[0])
    events: list[Conjunction] = []
    for a, b in pairs:
        ra, rb = positions[a], positions[b]
        valid = np.isfinite(ra[:, 0]) & np.isfinite(rb[:, 0])
        dist = np.where(valid, np.linalg.norm(ra - rb, axis=-1), np.inf)
        vrel = np.where(valid, np.linalg.norm(velocities[a] - velocities[b], axis=-1), 0.0)
        for k in _candidate_minima(dist, vrel, step, threshold_km):
            lo = offsets_s[max(k - 1, 0)]
            hi = offsets_s[min(k + 1, len(offsets_s) - 1)]
            try:
                t_tca = refine_tca(state_fns[a], state_fns[b], lo, hi)
                enc = describe_encounter(state_fns[a], state_fns[b], t_tca)
            except ValueError:
                continue
            if enc["miss_km"] >= threshold_km:
                continue
            events.append(Conjunction(
                id=event_id(a, b, t_tca, t0), primary_id=a, secondary_id=b, tca=iso(add_s(t0, t_tca)),
                tca_offset_s=round(t_tca, 3), level=level_for(enc["miss_km"]),
                **{k2: round(v2, 4) for k2, v2 in enc.items()},
            ))
    return events


def screen_catalog(catalog: Catalog, t0: datetime, horizon_s: float = 6 * 3600, step_s: float = 10.0,
                   threshold_km: float = 10.0, primaries: Optional[list[str]] = None) -> list[Conjunction]:
    """All-vs-all screening (or primaries-vs-all). Primary = the maneuverable object when possible."""
    offsets = np.arange(0.0, horizon_s + 1e-9, step_s)
    ids, r, v, err = catalog.propagate(t0, offsets)
    r = np.where(err[..., None] == 0, r, np.nan)
    pos = {i: r[n] for n, i in enumerate(ids)}
    vel = {i: v[n] for n, i in enumerate(ids)}
    fns = {i: sgp4_state_fn(catalog.objects[i], t0) for i in ids}

    pairs = []
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            a, b = ids[x], ids[y]
            if primaries is not None and a not in primaries and b not in primaries:
                continue
            # put the maneuverable / operator-owned object first
            if catalog.objects[b].maneuverable and not catalog.objects[a].maneuverable:
                a, b = b, a
            pairs.append((a, b))
    events = screen_pairs(t0, offsets, pos, vel, fns, pairs, threshold_km)
    return sorted(events, key=lambda e: e.miss_km)


def closest_approach(catalog: Catalog, a: str, b: str, t0: datetime, start_s: float, end_s: float,
                     step_s: float = 5.0) -> dict:
    """Global minimum separation between a and b inside [start_s, end_s] (offsets from t0)."""
    offsets = np.arange(start_s, end_s + 1e-9, step_s)
    ids, r, v, err = catalog.propagate(t0, offsets, [a, b])
    if err.any():
        raise ValueError("propagation error inside requested window")
    dist = np.linalg.norm(r[0] - r[1], axis=-1)
    k = int(np.argmin(dist))
    fa, fb = sgp4_state_fn(catalog.get(a), t0), sgp4_state_fn(catalog.get(b), t0)
    t_tca = refine_tca(fa, fb, offsets[max(k - 1, 0)], offsets[min(k + 1, len(offsets) - 1)])
    enc = describe_encounter(fa, fb, t_tca)
    return {"tca": iso(add_s(t0, t_tca)), "tca_offset_s": round(t_tca, 3), "level": level_for(enc["miss_km"]),
            **{k2: round(v2, 4) for k2, v2 in enc.items()}}
