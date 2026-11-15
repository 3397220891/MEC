"""Deterministic demo scenarios.

All objects here are SIMULATED (fictional names, synthetic SGP4 mean elements). The conjunction
scenarios engineer a secondary object whose SGP4 trajectory passes a chosen offset from the
primary at a chosen time, solved numerically with least squares, so the demo is reproducible.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import least_squares

from ..physics.constants import MU_EARTH, R_EARTH
from ..physics.propagation import Epoch, TrackedObject, make_satrec, ric_frame, state
from ..physics.screening import build_grid, screen_catalog

SCENARIO_EPOCH = datetime(2026, 11, 15, 6, 0, 0, tzinfo=timezone.utc)
HORIZON_S = 6 * 3600.0
STEP_S = 30.0

# name, kind, maneuverable, operator, alt_km, ecc, incl_deg
BASE_CATALOG = [
    ("OGX-1 HELIOS", "PAYLOAD", True, "OrbitalGuard Demo Ops", 505, 0.0011, 97.4),
    ("OGX-2 VEGA", "PAYLOAD", True, "OrbitalGuard Demo Ops", 530, 0.0008, 53.0),
    ("OGX-3 KESTREL", "PAYLOAD", True, "OrbitalGuard Demo Ops", 550, 0.0006, 53.0),
    ("OGX-4 NIMBUS", "PAYLOAD", True, "Simulated Weather Agency", 705, 0.0012, 98.2),
    ("OGX-5 MERIDIAN", "PAYLOAD", True, "Simulated Comms Co.", 780, 0.0009, 86.4),
    ("OGX-6 TERRA-SAR", "PAYLOAD", True, "Simulated EO Corp.", 620, 0.0010, 97.9),
    ("LYRA-1 CUBESAT", "PAYLOAD", False, "Simulated University", 420, 0.0015, 51.6),
    ("AURA-7", "PAYLOAD", True, "Simulated Science Agency", 680, 0.0014, 98.1),
    ("DEB FRAG-1123", "DEBRIS", False, "None (fragment)", 850, 0.0040, 71.0),
    ("DEB FRAG-0874", "DEBRIS", False, "None (fragment)", 760, 0.0060, 98.5),
    ("DEB FRAG-2201", "DEBRIS", False, "None (fragment)", 1100, 0.0030, 63.4),
    ("R/B STAGE-2 K4", "ROCKET_BODY", False, "None (spent stage)", 900, 0.0200, 74.0),
    ("R/B STAGE-2 M1", "ROCKET_BODY", False, "None (spent stage)", 640, 0.0150, 82.9),
    ("DEB FRAG-3310", "DEBRIS", False, "None (fragment)", 580, 0.0050, 65.1),
    ("DEB FRAG-0412", "DEBRIS", False, "None (fragment)", 470, 0.0070, 28.5),
    ("OGX-8 POLARIS", "PAYLOAD", True, "OrbitalGuard Demo Ops", 560, 0.0007, 97.6),
]


@dataclass
class EngineeredEncounter:
    name: str
    kind: str
    primary: str
    tca_s: float
    offset_ric_km: tuple[float, float, float]
    incl_deg: float
    ecc: float = 0.0008


@dataclass
class ScenarioDef:
    id: str
    title: str
    description: str
    encounters: list[EngineeredEncounter]


SCENARIOS: dict[str, ScenarioDef] = {
    "nominal": ScenarioDef(
        "nominal",
        "1 · Normal Operations",
        "16 simulated objects. Screening over a 6 h horizon finds no close approach inside the 5 km screening volume.",
        [],
    ),
    "conjunction": ScenarioDef(
        "conjunction",
        "2 · Potential Collision Event",
        "A debris fragment crosses OGX-3 KESTREL's orbit with a ~1.7 km predicted miss. Monitoring flags it and the agent investigates.",
        [EngineeredEncounter("DEB FRAG-2291", "DEBRIS", "OGX-3 KESTREL", 6720.0, (0.85, 0.30, 1.40), 97.6)],
    ),
    "maneuver": ScenarioDef(
        "maneuver",
        "3 · Maneuver Evaluation",
        "A defunct satellite is predicted to pass ~130 m from OGX-3 KESTREL in about 3 h. The agent runs a maneuver trade study.",
        [EngineeredEncounter("ORION-9 (DEFUNCT)", "PAYLOAD", "OGX-3 KESTREL", 11100.0, (0.065, 0.025, 0.105), 71.0)],
    ),
}


def _base_objects(epoch_dt: datetime, seed: int) -> list[TrackedObject]:
    rng = np.random.default_rng(seed)
    objs = []
    for k, (name, kind, man, op, alt, ecc, inc) in enumerate(BASE_CATALOG):
        sat = make_satrec(
            epoch_dt,
            90001 + k,
            alt,
            ecc,
            inc,
            raan_deg=float(rng.uniform(0, 360)),
            argp_deg=float(rng.uniform(0, 360)),
            mean_anomaly_deg=float(rng.uniform(0, 360)),
        )
        objs.append(
            TrackedObject(
                id=f"SIM-{90001 + k}",
                name=name,
                kind=kind,
                source="SIMULATED",
                satrec=sat,
                maneuverable=man,
                operator=op,
                norad_id=90001 + k,
            )
        )
    return objs


def engineer_secondary(primary: TrackedObject, epoch: Epoch, enc: EngineeredEncounter, satnum: int) -> TrackedObject:
    """Solve for (RAAN, mean anomaly, altitude) so SGP4 puts the object at primary + offset at tca."""
    ra, va = state(primary.satrec, epoch, enc.tca_s)
    target = ra + ric_frame(ra, va).T @ np.asarray(enc.offset_ric_km)

    rn = np.linalg.norm(target)
    dec, ra_ang = math.asin(target[2] / rn), math.atan2(target[1], target[0])
    inc = math.radians(enc.incl_deg)
    u = math.asin(max(-1.0, min(1.0, math.sin(dec) / math.sin(inc))))
    raan0 = ra_ang - math.atan2(math.cos(inc) * math.sin(u), math.cos(u))
    alt0 = rn - R_EARTH
    n = math.sqrt(MU_EARTH / rn**3)
    m0 = u - n * enc.tca_s

    def build(p):
        return make_satrec(epoch.dt, satnum, p[2], enc.ecc, enc.incl_deg, math.degrees(p[0]), 0.0, math.degrees(p[1]))

    def resid(p):
        r, _ = state(build(p), epoch, enc.tca_s)
        return r - target

    sol = least_squares(resid, x0=[raan0, m0, alt0], xtol=1e-13, ftol=1e-13, gtol=1e-13, max_nfev=200)
    err = float(np.linalg.norm(sol.fun))
    if err > 0.005:
        raise RuntimeError(f"Encounter engineering failed for {enc.name}: residual {err:.4f} km")
    return TrackedObject(
        id=f"SIM-{satnum}",
        name=enc.name,
        kind=enc.kind,
        source="SIMULATED",
        satrec=build(sol.x),
        maneuverable=False,
        operator="None (non-operational)",
        norad_id=satnum,
        notes="Engineered demo object (simulated).",
    )


_BASE_SEED: int | None = None


def _clean_seed(epoch: Epoch, report_km: float) -> int:
    """First RNG seed whose base catalog has no close approaches (deterministic)."""
    global _BASE_SEED
    if _BASE_SEED is None:
        for seed in range(7, 200):
            objs = _base_objects(epoch.dt, seed)
            grid = build_grid(objs, epoch, HORIZON_S, STEP_S)
            if not screen_catalog(objs, epoch, grid, report_km * 2):
                _BASE_SEED = seed
                break
        else:
            raise RuntimeError("No clean seed found")
    return _BASE_SEED


def build_scenario(scenario_id: str, report_km: float) -> tuple[Epoch, list[TrackedObject], ScenarioDef]:
    sdef = SCENARIOS[scenario_id]
    epoch = Epoch(SCENARIO_EPOCH)
    objs = _base_objects(epoch.dt, _clean_seed(epoch, report_km))
    by_name = {o.name: o for o in objs}
    for k, enc in enumerate(sdef.encounters):
        objs.append(engineer_secondary(by_name[enc.primary], epoch, enc, 99001 + k))
    return epoch, objs, sdef
