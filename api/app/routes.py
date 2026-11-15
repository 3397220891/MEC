"""OrbitalGuard REST API. Contract documented in docs/API.md."""
from __future__ import annotations

import hmac
import os
import time
from typing import Literal, Optional

import numpy as np
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from . import scenarios
from .agent.runner import Investigations
from .orbital.catalog import CatalogError
from .orbital.maneuver import ManeuverPlan, simulate_maneuver
from .orbital.timeutil import iso
from .world import World

router = APIRouter(prefix="/api")
_state: dict = {}


def init(world: World, investigations: Investigations) -> None:
    _state["world"], _state["inv"] = world, investigations


def W() -> World:
    return _state["world"]


def INV() -> Investigations:
    return _state["inv"]


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (CatalogError, KeyError) as e:
        raise HTTPException(404 if isinstance(e, KeyError) else 400, str(e).strip("'"))


# ---------------- scenarios & monitoring ----------------

@router.get("/scenarios")
def list_scenarios():
    return [s.__dict__ for s in scenarios.SCENARIOS]


@router.post("/scenarios/{scenario_id}/load")
def load_scenario(scenario_id: str, autostart: bool = True):
    status = _guard(W().load, scenario_id)
    inv_id = None
    if autostart and status["autostart_investigation"]:
        inv_id = _auto_investigate()
    return {**status, "auto_investigation_id": inv_id}


def _auto_investigate() -> Optional[str]:
    """Monitoring trigger: investigate the most severe RED/AMBER event that involves a maneuverable object."""
    w = W()
    for e in w.events:  # sorted by miss distance
        if e["level"] in ("RED", "AMBER") and w.catalog.get(e["primary_id"]).maneuverable:
            w.db.log(w.scenario_id, "alert", "monitor", f"Auto-triggering investigation for {e['id']} ({e['level']})")
            return INV().start(e["id"])
    return None


@router.get("/status")
def status():
    return W().status()


class ScreenIn(BaseModel):
    horizon_hours: Optional[float] = Field(None, gt=0, le=24)
    threshold_km: Optional[float] = Field(None, gt=0, le=50)


@router.post("/screen")
def rescreen(body: ScreenIn):
    return _guard(W().screen, body.horizon_hours * 3600 if body.horizon_hours else None, body.threshold_km)


@router.get("/objects")
def objects():
    return _guard(W().objects)


@router.get("/objects/{obj_id}")
def object_detail(obj_id: str):
    w = W()
    obj = _guard(w.catalog.get, obj_id) if w.catalog else None
    if obj is None:
        raise HTTPException(400, "no scenario loaded")
    return obj.summary(w.sim_now()) | {"meta": obj.meta}


@router.get("/objects/{obj_id}/trajectory")
def object_trajectory(obj_id: str, start_min: float = Query(0, ge=0, le=1440),
                      duration_min: float = Query(120, gt=0, le=1440), step_s: float = Query(30, ge=1, le=600)):
    w = W()
    _guard(w.catalog.get, obj_id)
    from .orbital.timeutil import add_s
    return w.catalog.trajectory(obj_id, add_s(w.t0, start_min * 60), duration_min, step_s)


@router.get("/trajectories")
def all_trajectories(duration_min: float = Query(360, gt=0, le=1440), step_s: float = Query(30, ge=5, le=600)):
    """Bulk TEME positions for the 3D view (km, rounded to 1 m). The client interpolates between samples."""
    w = W()
    if w.catalog is None:
        raise HTTPException(400, "no scenario loaded")
    offsets = np.arange(0, duration_min * 60 + 1e-9, step_s)
    ids, r, _, err = w.catalog.propagate(w.t0, offsets)
    r = np.where(err[..., None] == 0, r, np.nan)
    return {
        "t0": iso(w.t0), "step_s": step_s, "count": len(offsets),
        "objects": {i: np.round(np.nan_to_num(r[n], nan=0.0), 3).ravel().tolist() for n, i in enumerate(ids)},
    }


@router.get("/events")
def events():
    return W().events


@router.get("/logs")
def logs(limit: int = Query(150, ge=1, le=1000)):
    w = W()
    return w.db.logs(w.scenario_id, limit)


# ---------------- manual what-if (operator UI) ----------------

class ManualManeuverIn(BaseModel):
    event_id: str
    satellite_id: str
    burn_lead_time_min: float = Field(gt=0, le=720)
    dv_radial_m_s: float = Field(0, ge=-2, le=2)
    dv_along_track_m_s: float = Field(0, ge=-2, le=2)
    dv_normal_m_s: float = Field(0, ge=-2, le=2)
    label: str = Field("", max_length=80)


@router.post("/maneuvers/simulate")
def manual_maneuver(body: ManualManeuverIn):
    w = W()
    ev = _guard(w.event, body.event_id)
    plan = ManeuverPlan(body.satellite_id, ev["tca_offset_s"] - body.burn_lead_time_min * 60,
                        (body.dv_radial_m_s, body.dv_along_track_m_s, body.dv_normal_m_s), body.label)
    res = _guard(simulate_maneuver, w.catalog, w.t0, w.now_offset_s, ev, plan, w.horizon_s, 5.0, w.events)
    w.db.save_maneuver(res, None)
    return res


# ---------------- investigations & human approval ----------------

class InvestigateIn(BaseModel):
    event_id: str
    rule_based: bool = False
    use_cache: bool = True


@router.post("/investigations")
def start_investigation(body: InvestigateIn):
    return {"investigation_id": _guard(INV().start, body.event_id, body.rule_based, body.use_cache)}


@router.get("/investigations")
def list_investigations():
    w = W()
    return w.db.investigations(w.scenario_id)


@router.get("/investigations/{inv_id}")
def get_investigation(inv_id: str):
    db = W().db
    inv = db.investigation(inv_id)
    if not inv:
        raise HTTPException(404, f"unknown investigation {inv_id}")
    inv["steps"] = db.steps(inv_id)
    inv["maneuvers"] = db.maneuvers(inv_id=inv_id)
    return inv


@router.post("/investigations/{inv_id}/cancel")
def cancel_investigation(inv_id: str):
    INV().cancel(inv_id)
    return {"ok": True}


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    operator: str = Field(min_length=2, max_length=60)
    note: str = Field("", max_length=500)


@router.post("/investigations/{inv_id}/decision")
def decide(inv_id: str, body: DecisionIn, x_operator_token: Optional[str] = Header(None)):
    token = os.getenv("OPERATOR_TOKEN")
    if token and not hmac.compare_digest(token, x_operator_token or ""):
        raise HTTPException(401, "operator token required for approval decisions")
    w = W()
    inv = w.db.investigation(inv_id)
    if not inv:
        raise HTTPException(404, f"unknown investigation {inv_id}")
    if inv["status"] != "awaiting_approval":
        raise HTTPException(409, f"investigation is '{inv['status']}', not awaiting approval")
    decision = {
        "decision": body.decision, "operator": body.operator, "note": body.note, "decided_at": time.time(),
        "recommendation_decision": (inv["recommendation"] or {}).get("decision"),
        "maneuver_id": (inv["recommendation"] or {}).get("recommended_maneuver_id"),
        "disclaimer": "Recorded as a HYPOTHETICAL decision. OrbitalGuard has no spacecraft command capability.",
    }
    status = "approved" if body.decision == "approve" else "rejected"
    w.db.update_investigation(inv_id, status=status, decision=decision)
    w.db.log(inv["scenario"], "info", "approval", f"{inv_id} {status} by {body.operator}", decision)
    return {"status": status, "decision": decision}
