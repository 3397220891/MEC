"""Time and frame helpers (UTC <-> Julian date, TEME -> Earth-fixed -> geodetic)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4.api import jday

MU = 398600.4418  # km^3/s^2
RE = 6378.137  # km (WGS84 equatorial radius)
J2 = 1.08262668e-3
F_WGS84 = 1 / 298.257223563


def utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return utc(dt).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_iso(s: str) -> datetime:
    return utc(datetime.fromisoformat(s.replace("Z", "+00:00")))


def to_jd(dt: datetime) -> tuple[float, float]:
    dt = utc(dt)
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond / 1e6)
    return jd, fr


def jd_grid(t0: datetime, offsets_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Julian date arrays for t0 + offsets. sgp4 accepts fr outside [0, 1)."""
    jd, fr = to_jd(t0)
    offsets_s = np.asarray(offsets_s, dtype=float)
    return np.full(offsets_s.shape, jd), fr + offsets_s / 86400.0


def add_s(t0: datetime, seconds: float) -> datetime:
    return utc(t0) + timedelta(seconds=float(seconds))


def gmst_rad(jd_total: np.ndarray | float) -> np.ndarray | float:
    """Greenwich mean sidereal time (IAU-82), adequate for display and lat/lon."""
    t = (np.asarray(jd_total) - 2451545.0) / 36525.0
    sec = 67310.54841 + (876600 * 3600 + 8640184.812866) * t + 0.093104 * t**2 - 6.2e-6 * t**3
    return np.mod(np.deg2rad(sec / 240.0), 2 * math.pi)


def teme_to_ecef(r: np.ndarray, jd_total: np.ndarray | float) -> np.ndarray:
    """Rotate TEME positions (..., 3) about z by GMST. Polar motion ignored (<20 m)."""
    th = gmst_rad(jd_total)
    c, s = np.cos(th), np.sin(th)
    x = c * r[..., 0] + s * r[..., 1]
    y = -s * r[..., 0] + c * r[..., 1]
    return np.stack([x, y, r[..., 2]], axis=-1)


def ecef_to_geodetic(p: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """WGS84 lat/lon (deg) and altitude (km), Bowring's method (sub-metre for LEO)."""
    a = RE
    e2 = F_WGS84 * (2 - F_WGS84)
    b = a * (1 - F_WGS84)
    ep2 = (a**2 - b**2) / b**2
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    lon = np.arctan2(y, x)
    rho = np.hypot(x, y)
    th = np.arctan2(z * a, rho * b)
    lat = np.arctan2(z + ep2 * b * np.sin(th) ** 3, rho - e2 * a * np.cos(th) ** 3)
    n = a / np.sqrt(1 - e2 * np.sin(lat) ** 2)
    alt = rho / np.cos(lat) - n
    return np.rad2deg(lat), np.rad2deg(lon), alt


def rtn_basis(r: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rows: radial, transverse (along-track), normal unit vectors."""
    rh = r / np.linalg.norm(r)
    h = np.cross(r, v)
    nh = h / np.linalg.norm(h)
    th = np.cross(nh, rh)
    return np.vstack([rh, th, nh])
