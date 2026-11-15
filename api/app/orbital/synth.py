"""Builders for SIMULATED objects (demo scenarios). Every object built here is labelled source='simulated'."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Callable

import numpy as np
from scipy.optimize import least_squares
from sgp4.api import WGS72, Satrec

from .catalog import SpaceObject
from .timeutil import MU, RE, to_jd

StateFn = Callable[[float], tuple[np.ndarray, np.ndarray]]


def _epoch_days(t0: datetime) -> float:
    jd, fr = to_jd(t0)
    return jd + fr - 2433281.5  # days since 1949-12-31 00:00 UT, as sgp4init expects


def satrec_from_elements(satnum: int, t0: datetime, a_km: float, ecc: float, inc: float, raan: float,
                         argp: float, mean_anom: float, bstar: float = 1.5e-5) -> Satrec:
    n_rad_min = math.sqrt(MU / a_km**3) * 60.0
    sat = Satrec()
    sat.sgp4init(WGS72, "i", satnum, _epoch_days(t0), bstar, 0.0, 0.0, ecc, argp % (2 * math.pi), inc,
                 mean_anom % (2 * math.pi), n_rad_min, raan % (2 * math.pi))
    return sat


def circular(satnum: int, t0: datetime, alt_km: float, inc_deg: float, raan_deg: float, arglat_deg: float,
             ecc: float = 2e-4) -> Satrec:
    return satrec_from_elements(satnum, t0, RE + alt_km, ecc, math.radians(inc_deg), math.radians(raan_deg),
                                0.0, math.radians(arglat_deg))


def crossing_satrec(satnum: int, t0: datetime, target: StateFn, t_star: float, crossing_angle_deg: float,
                    miss_km: float, apsis: str = "perigee", dalt_km: float = 120.0) -> Satrec:
    """Build an object whose SGP4 trajectory passes `miss_km` (radially) from target(t_star) at t_star.

    The crossing happens at the object's perigee (or apogee), so its period differs from the target's and
    the encounter does not repeat every revolution.
    """
    p, vt = target(t_star)
    rmag = np.linalg.norm(p)
    rhat = p / rmag
    th = vt - np.dot(vt, rhat) * rhat
    th /= np.linalg.norm(th)
    nh = np.cross(rhat, th)
    ang = math.radians(crossing_angle_deg)
    direction = math.cos(ang) * th + math.sin(ang) * nh
    h = np.cross(rhat, direction)
    inc = math.acos(np.clip(h[2], -1, 1))
    raan = math.atan2(h[0], -h[1])
    node = np.array([math.cos(raan), math.sin(raan), 0.0])
    u = math.atan2(np.dot(np.cross(node, rhat), h), np.dot(node, rhat))
    if apsis == "perigee":
        a = rmag + dalt_km
        ecc = 1 - rmag / a
        nu = 0.0
    else:
        a = rmag - dalt_km
        ecc = rmag / a - 1
        nu = math.pi
    argp = u - nu
    n = math.sqrt(MU / a**3)
    m_star = 0.0 if apsis == "perigee" else math.pi
    m0 = m_star - n * t_star
    goal = p + miss_km * rhat
    jd, fr = to_jd(t0)

    def build(x):
        dm, dn, draan = x
        a_adj = (MU / (n * (1 + dn)) ** 2) ** (1 / 3)
        return satrec_from_elements(satnum, t0, a_adj, ecc, inc, raan + draan, argp, m0 + dm)

    def resid(x):
        err, r, _ = build(x).sgp4(jd, fr + t_star / 86400.0)
        return (np.array(r) - goal) if not err else np.full(3, 1e6)

    sol = least_squares(resid, x0=[0.0, 0.0, 0.0], x_scale=[1e-3, 1e-5, 1e-3], xtol=1e-14, ftol=1e-14)
    if np.linalg.norm(sol.fun) > 0.01:
        raise RuntimeError(f"crossing solver did not converge (residual {np.linalg.norm(sol.fun):.4f} km)")
    return build(sol.x)


def sim_object(obj_id: str, name: str, sat: Satrec, kind: str = "payload", operator: str | None = None,
               maneuverable: bool = False, mass_kg: float | None = None, **meta) -> SpaceObject:
    return SpaceObject(id=obj_id, name=name, satrec=sat, source="simulated", kind=kind, operator=operator,
                       maneuverable=maneuverable, mass_kg=mass_kg, meta=meta)
