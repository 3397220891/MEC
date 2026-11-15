"""Physics-engine tests: no network, deterministic."""
import numpy as np
import pytest

from app import scenarios as S
from app.orbital.catalog import SNAPSHOT, parse_tle_text, validate_tle
from app.orbital.maneuver import ManeuverPlan, compare_maneuvers, simulate_maneuver
from app.orbital.screening import closest_approach, screen_catalog

_LINES = SNAPSHOT.read_text(encoding="utf-8").splitlines()
ISS = (_LINES[0].strip(), _LINES[1], _LINES[2])  # first object in the bundled real snapshot


def _events(sid):
    cat, t0 = S.build(sid)
    return cat, t0, [e.to_dict() for e in screen_catalog(cat, t0, S.HORIZON_S, 10.0, 10.0)]


def test_tle_validation_catches_corruption():
    name, l1, l2 = ISS
    assert validate_tle(name, l1, l2) == []
    bad = l2[:20] + ("9" if l2[20] != "9" else "8") + l2[21:]
    assert "checksum mismatch" in validate_tle(name, l1, bad)


def test_snapshot_parses():
    objs, warnings = parse_tle_text(SNAPSHOT.read_text(encoding="utf-8"), "celestrak-snapshot")
    assert len(objs) >= 20
    assert all(o.source == "celestrak-snapshot" for o in objs.values())


def test_nominal_scenario_is_clean():
    _, _, events = _events("nominal")
    assert events == []


def test_conjunction_scenario_flags_red_event():
    _, _, events = _events("conjunction")
    assert len(events) == 1
    e = events[0]
    assert e["level"] == "RED" and {e["primary_id"], e["secondary_id"]} == {S.PRIMARY_ID, "SIM-DEB417"}
    assert abs(e["miss_km"] - S.DEBRIS_MISS_KM) < 0.005
    assert abs(e["tca_offset_s"] - S.DEBRIS_TCA_S) < 2
    assert 9 < e["rel_speed_km_s"] < 15


def test_screening_matches_dense_bruteforce():
    cat, t0, events = _events("conjunction")
    e = events[0]
    ca = closest_approach(cat, e["primary_id"], e["secondary_id"], t0, e["tca_offset_s"] - 30,
                          e["tca_offset_s"] + 30, step_s=0.05)
    assert abs(ca["miss_km"] - e["miss_km"]) < 1e-3


def test_zero_dv_reproduces_baseline():
    cat, t0, events = _events("conjunction")
    e = events[0]
    r = simulate_maneuver(cat, t0, 0, e, ManeuverPlan(S.PRIMARY_ID, e["tca_offset_s"] - 3000, (0, 0, 0)),
                          S.HORIZON_S, 5.0, events)
    assert abs(r["new_miss_km"] - e["miss_km"]) < 1e-3
    assert r["dv_magnitude_m_s"] == 0 and r["propellant_kg_est"] == 0


def test_prograde_burn_raises_orbit_and_clears_threat():
    cat, t0, events = _events("conjunction")
    e = events[0]
    r = simulate_maneuver(cat, t0, 0, e, ManeuverPlan(S.PRIMARY_ID, e["tca_offset_s"] - 5700, (0, 0.15, 0)),
                          S.HORIZON_S, 5.0, events)
    assert r["delta_sma_km"] > 0
    assert r["new_miss_km"] > 1.0


def test_scenario3_prograde_trap_and_retrograde_escape():
    cat, t0, events = _events("maneuver")
    main = next(e for e in events if e["secondary_id"] == "SIM-DEB417")
    tca = main["tca_offset_s"]
    trap = simulate_maneuver(cat, t0, 0, main, ManeuverPlan(S.PRIMARY_ID, tca - S.TRAP_PLAN_LEAD_S, S.TRAP_PLAN_DV),
                             S.HORIZON_S, 5.0, events)
    charlie = [s for s in trap["secondary_conjunctions"] if s["object_id"] == "SIM-CHARLIE"]
    assert charlie and charlie[0]["status"] == "worsened" and charlie[0]["level"] == "RED"
    retro = simulate_maneuver(cat, t0, 0, main, ManeuverPlan(S.PRIMARY_ID, tca - 5700, (0, -0.12, 0)),
                              S.HORIZON_S, 5.0, events)
    cmp = compare_maneuvers([trap, retro])
    assert cmp["best_option"] == retro["maneuver_id"]
    assert not cmp["options"][0]["meets_criteria"]


@pytest.mark.parametrize("plan,msg", [
    (ManeuverPlan(S.PRIMARY_ID, 100, (0, 0.1, 0)), "after the current"),
    (ManeuverPlan(S.PRIMARY_ID, 3000, (0, 3.0, 0)), "exceeds"),
    (ManeuverPlan("SIM-DEB417", 3000, (0, 0.1, 0)), "not flagged as maneuverable"),
])
def test_maneuver_validation(plan, msg):
    cat, t0, events = _events("conjunction")
    with pytest.raises(Exception, match=msg):
        simulate_maneuver(cat, t0, 0, events[0], plan, S.HORIZON_S, 5.0, events)


def test_trajectory_altitude_is_physical():
    cat, t0 = S.build("nominal")
    tr = cat.trajectory(S.PRIMARY_ID, t0, 100, 60)
    alt = np.array(tr["alt_km"])
    assert 520 < alt.min() and alt.max() < 580
