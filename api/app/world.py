"""Active mission state: loaded scenario, catalog, screening results, simulation clock."""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Optional

from . import scenarios
from .db import DB
from .orbital.catalog import Catalog, CatalogError, element_freshness
from .orbital.screening import screen_catalog
from .orbital.timeutil import add_s, iso

CO_ORBIT_REL_SPEED_KM_S = 0.1


class World:
    def __init__(self, db: DB):
        self.db = db
        self.lock = threading.RLock()
        self.scenario_id: Optional[str] = None
        self.catalog: Optional[Catalog] = None
        self.t0: Optional[datetime] = None
        self.now_offset_s = 0.0  # decision time ("now") relative to t0; burns must be after this
        self.horizon_s = scenarios.HORIZON_S
        self.step_s = 10.0
        self.threshold_km = 10.0
        self.events: list[dict] = []
        self.co_orbiting: list[tuple[str, str]] = []
        self.screen_ms = 0
        self.loaded_at = 0.0

    # ------------------------------------------------------------------
    def load(self, scenario_id: str) -> dict:
        with self.lock:
            catalog, t0 = scenarios.build(scenario_id)
            self.scenario_id, self.catalog, self.t0 = scenario_id, catalog, t0
            self.now_offset_s = 0.0
            self.loaded_at = time.time()
            for w in catalog.warnings:
                self.db.log(scenario_id, "warn", "data", w)
            self.db.log(scenario_id, "info", "data",
                        f"Loaded '{scenarios.info(scenario_id).name}': {len(catalog.objects)} objects "
                        f"from {catalog.source}", {"source_detail": catalog.source_detail})
            return self.screen()

    def screen(self, horizon_s: Optional[float] = None, threshold_km: Optional[float] = None) -> dict:
        with self.lock:
            self._require()
            if horizon_s:
                self.horizon_s = float(min(max(horizon_s, 600), 24 * 3600))
            if threshold_km:
                self.threshold_km = float(min(max(threshold_km, 0.5), 50))
            start = time.perf_counter()
            evs = screen_catalog(self.catalog, self.t0, self.horizon_s, self.step_s, self.threshold_km)
            self.screen_ms = int((time.perf_counter() - start) * 1000)
            # Docked modules / visiting vehicles / formation flyers share an orbit: tiny relative speed, recurring
            # minima every revolution. They are proximity operations, not conjunctions - report them separately.
            self.co_orbiting = sorted({tuple(sorted((e.primary_id, e.secondary_id))) for e in evs
                                       if e.rel_speed_km_s < CO_ORBIT_REL_SPEED_KM_S})
            self.events = [e.to_dict() for e in evs if e.rel_speed_km_s >= CO_ORBIT_REL_SPEED_KM_S]
            if self.co_orbiting:
                self.db.log(self.scenario_id, "info", "screening",
                            f"Excluded {len(self.co_orbiting)} co-orbiting pair(s) (relative speed < "
                            f"{CO_ORBIT_REL_SPEED_KM_S * 1000:.0f} m/s: docked modules or proximity operations)")
            n = len(self.catalog.objects)
            self.db.log(self.scenario_id, "info", "screening",
                        f"Screened {n * (n - 1) // 2} pairs over {self.horizon_s / 3600:.1f} h in {self.screen_ms} ms: "
                        f"{len(self.events)} event(s) under {self.threshold_km} km")
            for e in self.events:
                a, b = self.catalog.objects[e["primary_id"]], self.catalog.objects[e["secondary_id"]]
                self.db.log(self.scenario_id, "alert" if e["level"] in ("RED", "AMBER") else "info", "conjunction",
                            f"{e['level']} close approach {a.name} / {b.name}: {e['miss_km'] * 1000:.0f} m at {e['tca']}",
                            {"event_id": e["id"]})
            return self.status()

    # ------------------------------------------------------------------
    def _require(self) -> None:
        if self.catalog is None:
            raise CatalogError("no scenario loaded")

    def event(self, event_id: str) -> dict:
        with self.lock:
            for e in self.events:
                if e["id"] == event_id:
                    return e
        raise CatalogError(f"unknown event_id '{event_id}'")

    def sim_now(self) -> datetime:
        return add_s(self.t0, self.now_offset_s)

    def provenance(self) -> dict[str, Any]:
        self._require()
        info = scenarios.info(self.scenario_id)
        return {
            "scenario": self.scenario_id,
            "scenario_name": info.name,
            "simulated": info.simulated,
            "data_source": self.catalog.source,
            "data_source_detail": self.catalog.source_detail,
            "fetched_utc": self.catalog.fetched_utc,
            "scenario_start": iso(self.t0),
            "warnings": self.catalog.warnings[:10],
        }

    def status(self) -> dict[str, Any]:
        with self.lock:
            if self.catalog is None:
                return {"loaded": False}
            info = scenarios.info(self.scenario_id)
            return {
                "loaded": True,
                **self.provenance(),
                "description": info.description,
                "autostart_investigation": info.autostart_investigation,
                "now": iso(self.sim_now()),
                "horizon_s": self.horizon_s,
                "screen_step_s": self.step_s,
                "threshold_km": self.threshold_km,
                "screen_ms": self.screen_ms,
                "object_count": len(self.catalog.objects),
                "events": self.events,
                "co_orbiting_pairs": [list(p) for p in self.co_orbiting],
            }

    def objects(self) -> list[dict]:
        with self.lock:
            self._require()
            t = self.sim_now()
            flagged = {i for e in self.events if e["level"] in ("RED", "AMBER")
                       for i in (e["primary_id"], e["secondary_id"])}
            out = []
            for o in self.catalog.objects.values():
                s = o.summary(t)
                s["freshness"] = element_freshness(o, t)
                s["flagged"] = o.id in flagged
                out.append(s)
            return out
