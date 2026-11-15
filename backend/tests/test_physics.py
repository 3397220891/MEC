"""Guards the demo: engineered conjunctions must stay where the script expects them."""
import pytest

from app.physics.screening import build_grid, screen_catalog
from app.physics import maneuver as mnv
from app.scenarios.builder import HORIZON_S, STEP_S, build_scenario


@pytest.fixture(scope="module")
def scen():
    out = {}
    for sid in ("nominal", "conjunction", "maneuver"):
        ep, objs, _ = build_scenario(sid, 5.0)
        grid = build_grid(objs, ep, HORIZON_S, STEP_S)
        out[sid] = (ep, objs, grid, screen_catalog(objs, ep, grid, 5.0))
    return out


def test_nominal_has_no_events(scen):
    assert scen["nominal"][3] == []


def test_conjunction_event(scen):
    ev = min(scen["conjunction"][3], key=lambda e: e.miss_km)
    assert 1.0 < ev.miss_km < 2.5  # AMBER by design
    assert abs(ev.tca_s - 6720) < 60


def test_maneuver_event_is_red(scen):
    ev = min(scen["maneuver"][3], key=lambda e: e.miss_km)
    assert ev.miss_km < 0.5
    assert abs(ev.tca_s - 11100) < 60


def test_maneuver_increases_miss(scen):
    ep, objs, grid, events = scen["maneuver"]
    ev = min(events, key=lambda e: e.miss_km)
    by = {o.id: o for o in objs}
    a, b = by[ev.object_a], by[ev.object_b]
    if not a.maneuverable:
        a, b = b, a
    res = mnv.simulate(a, b, objs, ep, grid, ev.tca_s, ev.miss_km, 140.0, "prograde", 0.2, 5.0)
    assert res.new_miss_km > ev.miss_km + 0.5


def test_debris_cannot_maneuver(scen):
    ep, objs, grid, events = scen["maneuver"]
    ev = events[0]
    by = {o.id: o for o in objs}
    deb = by["SIM-99001"]
    with pytest.raises(mnv.ManeuverError):
        mnv.simulate(deb, by["SIM-90003"], objs, ep, grid, ev.tca_s, ev.miss_km, 30, "prograde", 0.1, 5.0)
