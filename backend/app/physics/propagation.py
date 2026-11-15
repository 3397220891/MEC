"""SGP4 propagation helpers.

All positions are in the TEME frame (km), times are seconds relative to a scenario epoch.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4.api import WGS72, Satrec, jday
from sgp4.propagation import gstime

from .constants import MU_EARTH, R_EARTH

JD_1949_12_31 = 2433281.5  # sgp4init epoch reference


@dataclass
class Epoch:
    """Scenario reference time."""

    dt: datetime

    @property
    def jd_fr(self) -> tuple[float, float]:
        d = self.dt
        return jday(d.year, d.month, d.day, d.hour, d.minute, d.second + d.microsecond * 1e-6)

    def at(self, t_s: float) -> datetime:
        return self.dt + timedelta(seconds=float(t_s))

    def iso(self, t_s: float = 0.0) -> str:
        return self.at(t_s).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def gmst(self, t_s: float = 0.0) -> float:
        jd, fr = self.jd_fr
        return float(gstime(jd + fr + t_s / 86400.0))


@dataclass
class TrackedObject:
    id: str
    name: str
    kind: str  # PAYLOAD | DEBRIS | ROCKET_BODY
    source: str  # SIMULATED | CELESTRAK_LIVE | CELESTRAK_SNAPSHOT
    satrec: Satrec
    maneuverable: bool = False
    operator: str = "Unknown"
    norad_id: int | None = None
    notes: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def tle_epoch(self) -> datetime:
        jd = self.satrec.jdsatepoch + self.satrec.jdsatepochF
        return datetime(1949, 12, 31, tzinfo=timezone.utc) + timedelta(days=jd - JD_1949_12_31)


class PropagationError(RuntimeError):
    pass


def make_satrec(
    epoch: datetime,
    satnum: int,
    alt_km: float,
    ecc: float,
    incl_deg: float,
    raan_deg: float,
    argp_deg: float,
    mean_anomaly_deg: float,
    bstar: float = 2.0e-5,
    mean_motion_rad_min: float | None = None,
) -> Satrec:
    """Build a synthetic SGP4 record from mean elements."""
    if mean_motion_rad_min is None:
        a = R_EARTH + alt_km
        mean_motion_rad_min = math.sqrt(MU_EARTH / a**3) * 60.0
    jd, fr = jday(epoch.year, epoch.month, epoch.day, epoch.hour, epoch.minute, epoch.second)
    sat = Satrec()
    sat.sgp4init(
        WGS72,
        "i",
        satnum,
        (jd + fr) - JD_1949_12_31,
        bstar,
        0.0,
        0.0,
        ecc,
        math.radians(argp_deg),
        math.radians(incl_deg),
        math.radians(mean_anomaly_deg) % (2 * math.pi),
        mean_motion_rad_min,
        math.radians(raan_deg) % (2 * math.pi),
    )
    return sat


def state(sat: Satrec, epoch: Epoch, t_s: float) -> tuple[np.ndarray, np.ndarray]:
    jd, fr = epoch.jd_fr
    e, r, v = sat.sgp4(jd, fr + t_s / 86400.0)
    if e != 0:
        raise PropagationError(f"SGP4 error code {e} at t={t_s:.1f}s")
    return np.asarray(r), np.asarray(v)


def states(sat: Satrec, epoch: Epoch, t_s: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    jd, fr = epoch.jd_fr
    t_s = np.asarray(t_s, dtype=float)
    jd_arr = np.full(t_s.shape, jd)
    fr_arr = fr + t_s / 86400.0
    e, r, v = sat.sgp4_array(jd_arr, fr_arr)
    return np.asarray(r), np.asarray(v), np.asarray(e)


def ric_frame(r: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rows are unit vectors Radial, In-track, Cross-track."""
    rh = r / np.linalg.norm(r)
    h = np.cross(r, v)
    ch = h / np.linalg.norm(h)
    ih = np.cross(ch, rh)
    return np.vstack([rh, ih, ch])


def elements(r: np.ndarray, v: np.ndarray) -> dict:
    """Osculating classical elements from a TEME state (for display/agent context)."""
    mu = MU_EARTH
    rn, vn = np.linalg.norm(r), np.linalg.norm(v)
    h = np.cross(r, v)
    hn = np.linalg.norm(h)
    n = np.cross([0.0, 0.0, 1.0], h)
    nn = np.linalg.norm(n)
    evec = ((vn**2 - mu / rn) * r - np.dot(r, v) * v) / mu
    ecc = float(np.linalg.norm(evec))
    energy = vn**2 / 2 - mu / rn
    a = -mu / (2 * energy)
    inc = math.degrees(math.acos(h[2] / hn))
    raan = math.degrees(math.atan2(n[1], n[0])) % 360 if nn > 1e-9 else 0.0
    if nn > 1e-9 and ecc > 1e-7:
        argp = math.degrees(math.acos(np.clip(np.dot(n, evec) / (nn * ecc), -1, 1)))
        if evec[2] < 0:
            argp = 360 - argp
    else:
        argp = 0.0
    period_min = 2 * math.pi * math.sqrt(a**3 / mu) / 60
    return {
        "semi_major_axis_km": round(a, 3),
        "eccentricity": round(ecc, 6),
        "inclination_deg": round(inc, 4),
        "raan_deg": round(raan, 4),
        "arg_perigee_deg": round(argp, 4),
        "period_min": round(period_min, 3),
        "perigee_alt_km": round(a * (1 - ecc) - R_EARTH, 2),
        "apogee_alt_km": round(a * (1 + ecc) - R_EARTH, 2),
    }


def perigee_apogee_alt(sat: Satrec) -> tuple[float, float]:
    """Mean-element perigee/apogee altitude (km), used for pair pre-filtering."""
    a = sat.a * R_EARTH  # sat.a is in Earth radii
    return a * (1 - sat.ecco) - R_EARTH, a * (1 + sat.ecco) - R_EARTH
