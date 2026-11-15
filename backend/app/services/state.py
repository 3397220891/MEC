"""Active mission state: loaded scenario, objects, propagation grid, detected events."""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

import numpy as np

from .. import config, db
from ..physics.constants import R_EARTH, TLE_ERROR_GROWTH_KM_PER_DAY, TLE_POSITION_ERROR_KM_AT_EPOCH
from ..physics.propagation import Epoch, TrackedObject, elements, state
from ..physics.screening import Grid, build_grid, screen_catalog
from ..scenarios.builder import HORIZON_S, SCENARIOS, STEP_S, build_scenario
from ..scenarios.catalog import load_real_catalog

SCENARIO_LIST = [
    *[{"id": s.id, "title": s.title, "description": s.description, "data": "SIMULATED"} for s in SCENARIOS.values()],
    {
        "id": "live",
        "title": "4 · Real Catalog (CelesTrak)",
        "description": "Up to 120 real objects from CelesTrak (falls back to a bundled snapshot offline). Screening over 6 h from now.",
        "data": "REAL",
    },
]


@dataclass
class Mission:
    scenario_id: str
    title: str
    epoch: Epoch
    objects: list[TrackedObject]
    grid: Grid
    events: list[dict]
    data_info: dict
    horizon_s: float = HORIZON_S
    step_s: float = STEP_S
    by_id: dict = field(default_factory=dict)

    def __post_init__(self):
        self.by_id = {o.id: o for o in self.objects}

    def obj(self, oid: str) -> TrackedObject:
        o = self.by_id.get(oid)
        if o is None:
            # tolerate lookups by exact name (agent convenience)
            o = next((x for x in self.objects if x.name.lower() == str(oid).lower()), None)
        if o is None:
            raise KeyError(f"Unknown satellite_id '{oid}'")
        return o

    def tle_age_days(self, o: TrackedObject, t_s: float = 0.0) -> float:
        return (self.epoch.at(t_s) - o.tle_epoch).total_seconds() / 86400.0

    def position_sigma_km(self, o: TrackedObject, t_s: float) -> float:
        return TLE_POSITION_ERROR_KM_AT_EPOCH + TLE_ERROR_GROWTH_KM_PER_DAY * abs(self.tle_age_days(o, t_s))

    def object_summary(self, o: TrackedObject, t_s: float = 0.0) -> dict:
        r, v = state(o.satrec, self.epoch, t_s)
        return {
            "id": o.id,
            "name": o.name,
            "kind": o.kind,
            "source": o.source,
            "data_label": "SIMULATED" if o.source == "SIMULATED" else "REAL",
            "maneuverable": o.maneuverable,
            "operator": o.operator,
            "norad_id": o.norad_id,
            "tle_epoch": o.tle_epoch.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tle_age_days_at_scenario_epoch": round(self.tle_age_days(o), 3),
            "altitude_km": round(float(np.linalg.norm(r) - R_EARTH), 2),
            "speed_km_s": round(float(np.linalg.norm(v)), 4),
            "elements": elements(r, v),
            "notes": o.notes,
        }


class MissionStore:
    def __init__(self):
        self._lock = threading.RLock()
        self.mission: Mission | None = None

    def load(self, scenario_id: str) -> Mission:
        with self._lock:
            if scenario_id == "live":
                epoch, objects, info = load_real_catalog()
                title = "4 · Real Catalog (CelesTrak)"
            elif scenario_id in SCENARIOS:
                epoch, objects, sdef = build_scenario(scenario_id, config.SCREEN_THRESHOLD_KM)
                title = sdef.title
                info = {"source": "SIMULATED", "count": len(objects), "note": "All objects are simulated for demonstration."}
            else:
                raise KeyError(f"Unknown scenario '{scenario_id}'")
            grid = build_grid(objects, epoch, HORIZON_S, STEP_S)
            approaches = screen_catalog(objects, epoch, grid, config.SCREEN_THRESHOLD_KM)
            by_id = {o.id: o for o in objects}
            events = []
            for k, ap in enumerate(approaches, start=1):
                a, b = by_id[ap.object_a], by_id[ap.object_b]
                # Primary = the maneuverable object, if any.
                if b.maneuverable and not a.maneuverable:
                    a, b = b, a
                ev = {
                    "id": f"EVT-{scenario_id[:3].upper()}-{k:03d}",
                    "scenario_id": scenario_id,
                    "primary_id": a.id,
                    "secondary_id": b.id,
                    "primary_name": a.name,
                    "secondary_name": b.name,
                    "tca_s": ap.tca_s,
                    "tca_utc": ap.tca_utc,
                    "miss_km": ap.miss_km,
                    "rel_speed_km_s": ap.rel_speed_km_s,
                    "miss_ric_km": ap.miss_ric_km,
                    "severity": config.severity(ap.miss_km),
                    "data_label": "SIMULATED" if a.source == "SIMULATED" else "REAL",
                    "detected_by": "SGP4 all-pairs screening",
                    "detected_at": db.now_iso(),
                }
                events.append(ev)
                db.execute(
                    "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (ev["id"], scenario_id, a.id, b.id, ap.tca_s, ap.tca_utc, ap.miss_km, ap.rel_speed_km_s, ev["severity"], db.dumps(ev), ev["detected_at"]),
                )
            self.mission = Mission(scenario_id, title, epoch, objects, grid, events, info)
            db.audit("system", "scenario_loaded", {"scenario": scenario_id, "objects": len(objects), "events": len(events), "data": info.get("source")})
            return self.mission

    def require(self) -> Mission:
        if self.mission is None:
            return self.load("nominal")
        return self.mission


STORE = MissionStore()


def ground_track(r_teme: np.ndarray, gmst: float) -> tuple[float, float]:
    """Approximate geodetic (spherical) lat/lon from TEME via GMST rotation."""
    x, y, z = r_teme
    lon = math.degrees(math.atan2(y, x) - gmst)
    lon = (lon + 180) % 360 - 180
    lat = math.degrees(math.atan2(z, math.hypot(x, y)))
    return lat, lon
