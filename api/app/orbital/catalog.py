"""Tracked-object catalog: TLE parsing, validation, live fetch with offline fallback."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import numpy as np
from sgp4.api import SGP4_ERRORS, Satrec, SatrecArray

from .timeutil import MU, RE, add_s, ecef_to_geodetic, iso, jd_grid, teme_to_ecef, to_jd, utc

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SNAPSHOT = DATA_DIR / "tle_snapshot.tle"
SNAPSHOT_META = DATA_DIR / "tle_snapshot.meta.json"
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php"


class CatalogError(ValueError):
    pass


@dataclass
class SpaceObject:
    id: str
    name: str
    satrec: Satrec
    source: str  # "celestrak-live" | "celestrak-snapshot" | "simulated"
    kind: str = "payload"  # payload | debris | rocket_body | unknown
    operator: Optional[str] = None
    mass_kg: Optional[float] = None
    maneuverable: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def simulated(self) -> bool:
        return self.source == "simulated"

    @property
    def epoch(self) -> datetime:
        jd = self.satrec.jdsatepoch + self.satrec.jdsatepochF
        return datetime(2000, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=jd - 2451545.0)

    def mean_elements(self) -> dict[str, float]:
        s = self.satrec
        n_rad_s = s.no_kozai / 60.0
        a = (MU / n_rad_s**2) ** (1 / 3)
        return {
            "semi_major_axis_km": round(a, 3),
            "eccentricity": round(s.ecco, 7),
            "inclination_deg": round(math.degrees(s.inclo), 4),
            "raan_deg": round(math.degrees(s.nodeo), 4),
            "arg_perigee_deg": round(math.degrees(s.argpo), 4),
            "mean_anomaly_deg": round(math.degrees(s.mo), 4),
            "mean_motion_rev_per_day": round(s.no_kozai * 1440 / (2 * math.pi), 8),
            "period_min": round(2 * math.pi / s.no_kozai, 3),
            "perigee_alt_km": round(a * (1 - s.ecco) - RE, 2),
            "apogee_alt_km": round(a * (1 + s.ecco) - RE, 2),
            "bstar": s.bstar,
        }

    def state(self, t: datetime) -> tuple[np.ndarray, np.ndarray]:
        jd, fr = to_jd(t)
        err, r, v = self.satrec.sgp4(jd, fr)
        if err:
            raise CatalogError(f"{self.id}: SGP4 error {err} ({SGP4_ERRORS.get(err, '?')}) at {iso(t)}")
        return np.array(r), np.array(v)

    def summary(self, t: datetime) -> dict[str, Any]:
        out = {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "operator": self.operator,
            "source": self.source,
            "simulated": self.simulated,
            "maneuverable": self.maneuverable,
            "tle_epoch": iso(self.epoch),
            "element_age_hours": round((utc(t) - self.epoch).total_seconds() / 3600, 2),
            "elements": self.mean_elements(),
        }
        try:
            r, v = self.state(t)
            jd, fr = to_jd(t)
            lat, lon, alt = ecef_to_geodetic(teme_to_ecef(r, jd + fr))
            out["state"] = {
                "time": iso(t),
                "position_teme_km": [round(x, 3) for x in r],
                "velocity_teme_km_s": [round(x, 5) for x in v],
                "altitude_km": round(float(alt), 2),
                "speed_km_s": round(float(np.linalg.norm(v)), 4),
                "lat_deg": round(float(lat), 3),
                "lon_deg": round(float(lon), 3),
            }
        except CatalogError as e:
            out["state"] = None
            out["state_error"] = str(e)
        if self.mass_kg:
            out["mass_kg"] = self.mass_kg
        return out


@dataclass
class Catalog:
    objects: dict[str, SpaceObject]
    source: str
    source_detail: str
    fetched_utc: Optional[str]
    warnings: list[str] = field(default_factory=list)

    def get(self, obj_id: str) -> SpaceObject:
        if obj_id not in self.objects:
            raise CatalogError(f"unknown satellite_id '{obj_id}'")
        return self.objects[obj_id]

    def ids(self) -> list[str]:
        return list(self.objects)

    def propagate(self, t0: datetime, offsets_s: np.ndarray, ids: Optional[list[str]] = None):
        """Vectorised SGP4. Returns ids, r (N,T,3) km, v (N,T,3) km/s, err (N,T)."""
        ids = ids or self.ids()
        arr = SatrecArray([self.objects[i].satrec for i in ids])
        jd, fr = jd_grid(t0, offsets_s)
        err, r, v = arr.sgp4(jd, fr)
        return ids, r, v, err

    def trajectory(self, obj_id: str, t0: datetime, minutes: float, step_s: float) -> dict[str, Any]:
        offsets = np.arange(0, minutes * 60 + 1e-9, step_s)
        _, r, v, err = self.propagate(t0, offsets, [obj_id])
        jd, fr = jd_grid(t0, offsets)
        ecef = teme_to_ecef(r[0], jd + fr)
        lat, lon, alt = ecef_to_geodetic(ecef)
        ok = err[0] == 0
        return {
            "id": obj_id,
            "start": iso(t0),
            "step_s": step_s,
            "offsets_s": offsets[ok].tolist(),
            "teme_km": np.round(r[0][ok], 3).tolist(),
            "alt_km": np.round(alt[ok], 2).tolist(),
            "lat_deg": np.round(lat[ok], 3).tolist(),
            "lon_deg": np.round(lon[ok], 3).tolist(),
            "speed_km_s": np.round(np.linalg.norm(v[0][ok], axis=-1), 4).tolist(),
            "propagation_errors": int((~ok).sum()),
        }


# ---------------- TLE parsing & validation ----------------

def _checksum_ok(line: str) -> bool:
    if len(line) < 69 or not line[68].isdigit():
        return False
    total = sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line[:68])
    return total % 10 == int(line[68])


def validate_tle(name: str, l1: str, l2: str) -> list[str]:
    problems = []
    if not (l1.startswith("1 ") and l2.startswith("2 ")):
        problems.append("line prefixes are not '1 ' / '2 '")
    if not _checksum_ok(l1) or not _checksum_ok(l2):
        problems.append("checksum mismatch")
    if l1[2:7] != l2[2:7]:
        problems.append("catalog numbers differ between lines")
    return problems


def _kind_from_name(name: str) -> str:
    n = name.upper()
    if " DEB" in n or n.endswith("DEB"):
        return "debris"
    if "R/B" in n:
        return "rocket_body"
    return "payload"


def parse_tle_text(text: str, source: str, ref_time: Optional[datetime] = None,
                   max_age_days: float = 30.0) -> tuple[dict[str, SpaceObject], list[str]]:
    lines = [ln.rstrip() for ln in text.replace("\r", "").split("\n") if ln.strip()]
    objects: dict[str, SpaceObject] = {}
    warnings: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("1 ") and i + 1 < len(lines):  # 2-line format without a name line
            name, l1, l2 = None, lines[i], lines[i + 1]
            i += 2
        elif i + 2 < len(lines):  # 3-line format
            name, l1, l2 = lines[i].strip(), lines[i + 1], lines[i + 2]
            i += 3
        else:
            warnings.append(f"ignored {len(lines) - i} trailing line(s)")
            break
        problems = validate_tle(name or "", l1, l2)
        if problems:
            warnings.append(f"rejected {name or l1[2:7]}: {', '.join(problems)}")
            continue
        sat = Satrec.twoline2rv(l1, l2)
        if sat.error:
            warnings.append(f"rejected {name}: sgp4 init error {sat.error}")
            continue
        if sat.no_kozai * 1440 / (2 * math.pi) < 11.25:  # not LEO: this demo screens LEO only
            continue
        obj_id = l1[2:7].strip()
        obj = SpaceObject(id=obj_id, name=name or obj_id, satrec=sat, source=source, kind=_kind_from_name(name or ""))
        if ref_time is not None:
            age = abs((utc(ref_time) - obj.epoch).total_seconds()) / 86400
            if age > max_age_days:
                warnings.append(f"rejected {obj.name}: elements are {age:.0f} days old")
                continue
        objects[obj_id] = obj
    return objects, warnings


def load_live_or_snapshot(groups: tuple[str, ...] = ("stations", "visual"), limit: int = 60,
                          timeout_s: float = 8.0) -> Catalog:
    """Try CelesTrak; on any failure fall back to the bundled snapshot. Never raises for network issues."""
    warnings: list[str] = []
    now = datetime.now(timezone.utc)
    text = ""
    try:
        with httpx.Client(timeout=timeout_s, headers={"User-Agent": "OrbitalGuard-hackathon/0.1"}) as client:
            for g in groups:
                resp = client.get(CELESTRAK_URL, params={"GROUP": g, "FORMAT": "tle"})
                resp.raise_for_status()
                if "\n1 " not in resp.text:  # CelesTrak returns 200 + plain-text errors for bad queries
                    raise CatalogError(f"unexpected CelesTrak response for group {g}")
                text += resp.text + "\n"
        objects, w = parse_tle_text(text, "celestrak-live", ref_time=now)
        warnings += w
        if len(objects) < 5:
            raise CatalogError("live fetch returned too few valid objects")
        objects = dict(list(objects.items())[:limit])
        return Catalog(objects, "celestrak-live", f"CelesTrak GP groups {', '.join(groups)}", iso(now), warnings)
    except (httpx.HTTPError, CatalogError) as e:
        warnings.append(f"live fetch failed ({type(e).__name__}: {e}); using bundled snapshot")
    meta = json.loads(SNAPSHOT_META.read_text(encoding="utf-8")) if SNAPSHOT_META.exists() else {}
    objects, w = parse_tle_text(SNAPSHOT.read_text(encoding="utf-8"), "celestrak-snapshot")
    warnings += w
    objects = dict(list(objects.items())[:limit])
    return Catalog(objects, "celestrak-snapshot", meta.get("source", "bundled snapshot"),
                   meta.get("fetched_utc"), warnings)


def element_freshness(obj: SpaceObject, t: datetime) -> str:
    hours = abs((utc(t) - obj.epoch).total_seconds()) / 3600
    if hours < 24:
        return "fresh (<24 h)"
    if hours < 72:
        return "aging (1-3 days)"
    return "stale (>3 days) - SGP4 error grows roughly 1-3 km/day in LEO"
