"""Maneuver plan for the globe must match the numbers the agent reported."""
import numpy as np
import pytest

from app import db
from app.agent import runner
from app.agent.offline import run_offline
from app.agent.tools import InvestigationContext
from app.physics.propagation import state
from app.services import plan
from app.services.state import MissionStore


@pytest.fixture()
def mission_inv(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "plan.db")
    monkeypatch.setattr(db, "_conn", None)
    m = MissionStore().load("maneuver")
    ev = min(m.events, key=lambda e: e["miss_km"])
    db.execute("INSERT INTO investigations (id, event_id, scenario_id, status, mode, model, started_at) VALUES (?,?,?,?,?,?,?)",
               ("INV-P", ev["id"], m.scenario_id, "running", "fallback", "deterministic-planner", db.now_iso()))
    ctx = InvestigationContext(id="INV-P", mission=m, event=ev)
    runner._finalize(ctx, run_offline(ctx, "test"), "fallback")
    return m, db.query_one("SELECT * FROM investigations WHERE id = 'INV-P'")


def _pos(path, t):
    i = int(round(t / path["step_s"]))
    return np.array(path["positions"][i * 3:i * 3 + 3])


def test_plan_lists_options_and_recommended_path(mission_inv):
    m, inv = mission_inv
    p = plan.build_plan(m, inv)
    rec = p["recommended_maneuver_id"]
    assert rec and any(o["recommended"] and o["maneuver_id"] == rec for o in p["options"])
    assert all(o["burn_time_s"] < p["tca_s"] and o["label"].startswith("T−") for o in p["options"])
    assert p["path"]["maneuver_id"] == rec and len(p["path"]["positions"]) == 3 * p["path"]["n"]


def test_drawn_path_reproduces_reported_miss(mission_inv):
    m, inv = mission_inv
    p = plan.build_plan(m, inv, step_s=10)
    chosen = next(o for o in p["options"] if o["maneuver_id"] == p["path"]["maneuver_id"])
    sec = m.obj(p["secondary_id"])
    # exact position at the new TCA returned by the plan
    sec_at = np.asarray(state(sec.satrec, m.epoch, chosen["new_tca_s"])[0])
    assert np.linalg.norm(np.array(p["path"]["new_tca_position_km"]) - sec_at) == pytest.approx(chosen["new_miss_km"], abs=0.01)
    # before the burn the path is the unmodified orbit; well after it, it has moved away
    prim = m.obj(p["primary_id"])
    t_before = (chosen["burn_time_s"] // 10 - 1) * 10
    assert np.allclose(_pos(p["path"], t_before), state(prim.satrec, m.epoch, t_before)[0], atol=0.02)
    t_after = (p["tca_s"] // 10) * 10
    assert np.linalg.norm(_pos(p["path"], t_after) - state(prim.satrec, m.epoch, t_after)[0]) > 1.0


def test_unknown_maneuver_is_rejected(mission_inv):
    m, inv = mission_inv
    with pytest.raises(plan.PlanError):
        plan.build_plan(m, inv, maneuver_id="M999")
