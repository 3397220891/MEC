"""Close-approach screening: coarse grid search + bounded TCA refinement.

This is *screening* (miss distance from point estimates), not a collision probability
assessment. TLEs carry no covariance, so Pc cannot be computed honestly here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Callable

import numpy as np
from scipy.optimize import minimize_scalar

from .propagation import Epoch, TrackedObject, perigee_apogee_alt, ric_frame, state, states

MAX_REL_SPEED_KM_S = 16.0  # upper bound for LEO crossing geometry
ALT_PAD_KM = 25.0


@dataclass
class Approach:
    object_a: str
    object_b: str
    tca_s: float
    tca_utc: str
    miss_km: float
    rel_speed_km_s: float
    miss_ric_km: dict  # relative position of B wrt A in A's RIC frame
    approach_angle_deg: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Grid:
    t: np.ndarray  # (T,)
    pos: np.ndarray  # (N, T, 3)
    ids: list[str]
    ok: np.ndarray  # (N, T) bool


def build_grid(objects: list[TrackedObject], epoch: Epoch, horizon_s: float, step_s: float) -> Grid:
    t = np.arange(0.0, horizon_s + 1e-9, step_s)
    pos = np.zeros((len(objects), len(t), 3))
    ok = np.zeros((len(objects), len(t)), dtype=bool)
    for i, obj in enumerate(objects):
        r, _, e = states(obj.satrec, epoch, t)
        pos[i] = r
        ok[i] = (e == 0) & np.all(np.isfinite(r), axis=1)
    return Grid(t=t, pos=pos, ids=[o.id for o in objects], ok=ok)


def candidate_pairs(objects: list[TrackedObject]) -> list[tuple[int, int]]:
    """Apogee/perigee filter: orbits whose altitude shells never overlap cannot meet."""
    shells = [perigee_apogee_alt(o.satrec) for o in objects]
    pairs = []
    for i in range(len(objects)):
        for j in range(i + 1, len(objects)):
            (pi, ai), (pj, aj) = shells[i], shells[j]
            if max(pi, pj) - min(ai, aj) <= ALT_PAD_KM:
                pairs.append((i, j))
    return pairs


def coarse_minima(d: np.ndarray, coarse_km: float) -> list[int]:
    """Indices of local minima in a sampled distance series below a coarse threshold."""
    idx = []
    n = len(d)
    for k in range(n):
        left = d[k - 1] if k > 0 else np.inf
        right = d[k + 1] if k < n - 1 else np.inf
        if d[k] <= left and d[k] <= right and d[k] < coarse_km:
            idx.append(k)
    return idx


def refine_tca(dist_fn: Callable[[float], float], t_lo: float, t_hi: float) -> tuple[float, float]:
    res = minimize_scalar(dist_fn, bounds=(t_lo, t_hi), method="bounded", options={"xatol": 1e-3})
    return float(res.x), float(res.fun)


def describe_approach(
    a: TrackedObject,
    b: TrackedObject,
    epoch: Epoch,
    tca: float,
    pos_a_fn: Callable[[float], tuple[np.ndarray, np.ndarray]] | None = None,
) -> Approach:
    ra, va = pos_a_fn(tca) if pos_a_fn else state(a.satrec, epoch, tca)
    rb, vb = state(b.satrec, epoch, tca)
    dr, dv = rb - ra, vb - va
    ric = ric_frame(ra, va) @ dr
    cosang = np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb))
    return Approach(
        object_a=a.id,
        object_b=b.id,
        tca_s=round(tca, 3),
        tca_utc=epoch.iso(tca),
        miss_km=round(float(np.linalg.norm(dr)), 4),
        rel_speed_km_s=round(float(np.linalg.norm(dv)), 4),
        miss_ric_km={"radial": round(float(ric[0]), 4), "in_track": round(float(ric[1]), 4), "cross_track": round(float(ric[2]), 4)},
        approach_angle_deg=round(math.degrees(math.acos(float(np.clip(cosang, -1, 1)))), 2),
    )


def pair_dist_fn(a: TrackedObject, b: TrackedObject, epoch: Epoch, offset_fn=None) -> Callable[[float], float]:
    def f(t: float) -> float:
        ra, _ = state(a.satrec, epoch, t)
        if offset_fn is not None:
            ra = ra + offset_fn(t)
        rb, _ = state(b.satrec, epoch, t)
        return float(np.linalg.norm(ra - rb))

    return f


def screen_pair(
    a: TrackedObject,
    b: TrackedObject,
    epoch: Epoch,
    t: np.ndarray,
    pa: np.ndarray,
    pb: np.ndarray,
    report_km: float,
) -> list[Approach]:
    step = float(t[1] - t[0])
    coarse_km = report_km + 0.5 * step * MAX_REL_SPEED_KM_S
    d = np.linalg.norm(pa - pb, axis=1)
    out = []
    fn = pair_dist_fn(a, b, epoch)
    for k in coarse_minima(d, coarse_km):
        lo, hi = max(t[0], t[k] - step), min(t[-1], t[k] + step)
        tca, miss = refine_tca(fn, lo, hi)
        if miss <= report_km:
            out.append(describe_approach(a, b, epoch, tca))
    return out


def screen_catalog(objects: list[TrackedObject], epoch: Epoch, grid: Grid, report_km: float) -> list[Approach]:
    """All-pairs screening over the grid horizon. Excludes co-located (docked/formation) pairs."""
    results: list[Approach] = []
    for i, j in candidate_pairs(objects):
        mask = grid.ok[i] & grid.ok[j]
        if mask.sum() < 3:
            continue
        found = screen_pair(objects[i], objects[j], epoch, grid.t, grid.pos[i], grid.pos[j], report_km)
        results.extend(x for x in found if x.rel_speed_km_s > 0.05)
    results.sort(key=lambda x: (x.tca_s, x.miss_km))
    return results


def separation_series(
    a: TrackedObject, b: TrackedObject, epoch: Epoch, t_center: float, half_window_s: float, n: int = 121, offset_fn=None
) -> list[dict]:
    ts = np.linspace(t_center - half_window_s, t_center + half_window_s, n)
    ra, _, _ = states(a.satrec, epoch, ts)
    rb, _, _ = states(b.satrec, epoch, ts)
    if offset_fn is not None:
        ra = ra + np.array([offset_fn(x) for x in ts])
    d = np.linalg.norm(ra - rb, axis=1)
    return [{"t_s": round(float(x), 2), "dt_from_tca_s": round(float(x - t_center), 2), "sep_km": round(float(y), 4)} for x, y in zip(ts, d)]
