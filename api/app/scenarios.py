"""Deterministic demo scenarios + the live-catalog mode.

All scenario objects are SIMULATED and labelled as such everywhere they appear
(API payloads, UI badges, agent tool outputs, reports).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Callable

from .orbital.catalog import Catalog, load_live_or_snapshot
from .orbital.maneuver import ManeuverPlan, ManeuveredTrajectory
from .orbital.screening import sgp4_state_fn
from .orbital.synth import circular, crossing_satrec, sim_object

SCENARIO_EPOCH = datetime(2026, 11, 16, 0, 0, 0, tzinfo=timezone.utc)
HORIZON_S = 6 * 3600
OPERATOR = "OrbitalGuard Demo Ops (SIMULATED)"

# Conjunction geometry shared by scenarios 2 and 3
PRIMARY_ID = "SIM-OGS2"
DEBRIS_TCA_S = 2 * 3600 + 20 * 60  # T0 + 02:20:00
DEBRIS_MISS_KM = 0.162
# Scenario 3: a third object sits on the path a "naive" prograde burn would take
TRAP_PLAN_LEAD_S = 5700.0
TRAP_PLAN_DV = (0.0, 0.08, 0.0)
TRAP_TCA_S = DEBRIS_TCA_S + 40 * 60
TRAP_MISS_KM = 0.30


@dataclass(frozen=True)
class ScenarioInfo:
    id: str
    name: str
    description: str
    simulated: bool
    autostart_investigation: bool


SCENARIOS = [
    ScenarioInfo("nominal", "1 · Normal operations",
                 "Simulated 4-satellite operator fleet plus third-party objects. No close approaches within the "
                 "screening threshold over the 6-hour horizon.", True, False),
    ScenarioInfo("conjunction", "2 · Potential collision",
                 "A simulated debris fragment crosses SIM-OGS2's orbit at ~10.6 km/s. Screening flags a RED "
                 "event and the agent investigation starts automatically.", True, True),
    ScenarioInfo("maneuver", "3 · Maneuver trade-off",
                 "Same debris threat, plus a second object near SIM-OGS2's orbit. One plausible avoidance burn "
                 "moves the satellite into it; the agent has to notice this when it compares options.", True, True),
    ScenarioInfo("live", "Live catalog (real data)",
                 "Real CelesTrak elements (bundled snapshot if offline). Screening on real objects; maneuvers "
                 "are hypothetical what-ifs only.", False, False),
]


def _fleet(t0: datetime):
    objs = [
        sim_object("SIM-OGS1", "OGS-1 (SIM)", circular(90001, t0, 530, 53.0, 10, 0), operator=OPERATOR,
                   maneuverable=True, mass_kg=310),
        sim_object(PRIMARY_ID, "OGS-2 (SIM)", circular(90002, t0, 550, 53.0, 100, 40), operator=OPERATOR,
                   maneuverable=True, mass_kg=310),
        sim_object("SIM-OGS3", "OGS-3 (SIM)", circular(90003, t0, 575, 53.0, 190, 200), operator=OPERATOR,
                   maneuverable=True, mass_kg=310),
        sim_object("SIM-OGS4", "OGS-4 (SIM)", circular(90004, t0, 600, 53.0, 280, 300), operator=OPERATOR,
                   maneuverable=True, mass_kg=310),
        sim_object("SIM-SSO1", "EARTHWATCH-A (SIM)", circular(90011, t0, 705, 98.2, 45, 120), operator="Other (SIM)"),
        sim_object("SIM-SSO2", "EARTHWATCH-B (SIM)", circular(90012, t0, 786, 98.5, 225, 10), operator="Other (SIM)"),
        sim_object("SIM-RB1", "CZ-SIM R/B", circular(90021, t0, 640, 82.0, 320, 250), kind="rocket_body"),
        sim_object("SIM-DEB1", "FRAG-112 (SIM)", circular(90031, t0, 830, 74.0, 150, 60), kind="debris"),
        sim_object("SIM-DEB2", "FRAG-208 (SIM)", circular(90032, t0, 905, 65.0, 60, 170), kind="debris"),
        sim_object("SIM-GEOD", "GEODESY-1 (SIM)", circular(90041, t0, 470, 28.5, 200, 90), operator="Other (SIM)"),
    ]
    return {o.id: o for o in objs}


def _catalog(objs: dict, detail: str) -> Catalog:
    return Catalog(objs, "simulated", detail, None, [])


@lru_cache(maxsize=None)
def _build_simulated(scenario_id: str) -> Catalog:
    t0 = SCENARIO_EPOCH
    objs = _fleet(t0)
    if scenario_id == "nominal":
        return _catalog(objs, "Synthetic SGP4 elements, scenario 1")
    primary = objs[PRIMARY_ID]
    deb = crossing_satrec(90099, t0, sgp4_state_fn(primary, t0), DEBRIS_TCA_S, 95.0, DEBRIS_MISS_KM,
                          apsis="perigee", dalt_km=140)
    objs["SIM-DEB417"] = sim_object("SIM-DEB417", "FRAG-417 (SIM)", deb, kind="debris",
                                    note="fragment from simulated breakup event; tuned to pass 162 m from OGS-2")
    if scenario_id == "conjunction":
        return _catalog(objs, "Synthetic SGP4 elements, scenario 2")
    trap_plan = ManeuverPlan(PRIMARY_ID, DEBRIS_TCA_S - TRAP_PLAN_LEAD_S, TRAP_PLAN_DV)
    traj = ManeuveredTrajectory(_catalog(objs, ""), t0, trap_plan, TRAP_TCA_S + 600)
    trap = crossing_satrec(90098, t0, traj.state, TRAP_TCA_S, 80.0, TRAP_MISS_KM, apsis="apogee", dalt_km=110)
    objs["SIM-CHARLIE"] = sim_object("SIM-CHARLIE", "CUBESAT-CHARLIE (SIM)", trap, operator="University (SIM)",
                                     note="non-maneuverable cubesat")
    return _catalog(objs, "Synthetic SGP4 elements, scenario 3")


def build(scenario_id: str) -> tuple[Catalog, datetime]:
    """Return (catalog, scenario start time)."""
    if scenario_id == "live":
        cat = load_live_or_snapshot()
        for o in cat.objects.values():
            if o.kind == "payload":
                o.maneuverable = True
                o.meta["maneuver_note"] = "assumed maneuverable for hypothetical what-if analysis only"
        if cat.source == "celestrak-live":
            return cat, datetime.now(timezone.utc).replace(microsecond=0)
        # snapshot: replay at fetch time so the element age stays realistic
        from .orbital.timeutil import parse_iso
        return cat, parse_iso(cat.fetched_utc) if cat.fetched_utc else datetime.now(timezone.utc)
    if scenario_id not in {s.id for s in SCENARIOS}:
        raise KeyError(scenario_id)
    return _build_simulated(scenario_id), SCENARIO_EPOCH


def info(scenario_id: str) -> ScenarioInfo:
    return next(s for s in SCENARIOS if s.id == scenario_id)


Builder = Callable[[str], tuple[Catalog, datetime]]
