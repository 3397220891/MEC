"""OrbitalGuard AI — FastAPI application."""
from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import config, db
from .agent import chat, chat_stream, runner
from .physics import maneuver as mnv
from .physics.propagation import state, states
from .physics.screening import separation_series
from .services import health as health_svc
from .services import plan
from .services.state import SCENARIO_LIST, STORE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("orbitalguard")


def _auto_investigate(mission) -> list[str]:
    started = []
    for ev in mission.events:
        if ev["severity"] in ("RED", "AMBER"):
            started.append(runner.start_investigation(ev["id"], trigger="auto-monitor"))
    return started


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.conn()
    mission = STORE.load("maneuver")
    for event in mission.events:
        if event["severity"] not in ("RED", "AMBER"):
            continue
        latest = db.query_one(
            "SELECT status FROM investigations WHERE event_id = ? ORDER BY started_at DESC LIMIT 1",
            (event["id"],),
        )
        if not latest or latest["status"] != "awaiting_approval":
            runner.start_investigation(event["id"], trigger="auto-monitor")
    log.info("OrbitalGuard ready. LLM agent configured: %s", config.llm_configured())
    yield


app = FastAPI(title="OrbitalGuard AI", version="0.1.0", lifespan=lifespan,
              description="Satellite close-approach screening and explainable AI investigation. Advisory only.")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["*"])


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """Any unexpected error becomes a readable JSON error with a reference the operator can quote."""
    ref = uuid.uuid4().hex[:8]
    log.exception("unhandled error %s on %s %s", ref, request.method, request.url.path)
    return JSONResponse(status_code=500, content={
        "detail": f"Internal error ({type(exc).__name__}) while handling {request.url.path}. Reference {ref}; details are in the server log.",
        "error": "internal_error", "reference": ref, "path": request.url.path})


def _mission():
    return STORE.require()


def _event(event_id: str) -> dict:
    ev = next((e for e in _mission().events if e["id"] == event_id), None)
    if ev is None:
        raise HTTPException(404, f"event '{event_id}' not found in active scenario")
    return ev


# ------------------------------------------------------------------ system

@app.get("/api/health")
def health(probe: bool = False):
    """llm_available = an API key is configured; llm.state = whether the LLM is actually reachable right now."""
    m = STORE.mission
    return {"status": "ok", "llm_available": config.llm_configured(), "model": config.MODEL if config.llm_configured() else None,
            "llm": health_svc.llm_status(force=probe),
            "scenario": m.scenario_id if m else None, "data_source": m.data_info.get("source") if m else None,
            "actuation_capability": False}


@app.get("/api/scenarios")
def scenarios():
    return SCENARIO_LIST


@app.post("/api/scenarios/{scenario_id}/load")
def load_scenario(scenario_id: str, auto_investigate: bool = True):
    try:
        m = STORE.load(scenario_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    started = _auto_investigate(m) if auto_investigate else []
    return {"scenario_id": m.scenario_id, "objects": len(m.objects), "events": len(m.events), "investigations_started": started}


@app.get("/api/state")
def get_state():
    m = _mission()
    invs = db.query("SELECT id, event_id, status, mode, model, started_at, finished_at FROM investigations WHERE scenario_id = ? ORDER BY started_at DESC", (m.scenario_id,))
    return {
        "scenario_id": m.scenario_id,
        "title": m.title,
        "epoch_utc": m.epoch.iso(),
        "gmst0_rad": m.epoch.gmst(0.0),
        "horizon_s": m.horizon_s,
        "data": m.data_info,
        "llm_available": config.llm_configured(),
        "thresholds_km": {"screen": config.SCREEN_THRESHOLD_KM, "red": config.RED_KM, "amber": config.AMBER_KM},
        "objects": [m.object_summary(o) for o in m.objects],
        "events": m.events,
        "investigations": invs,
    }


@app.get("/api/objects/{object_id}")
def get_object(object_id: str, t_s: float = Query(0.0, ge=0)):
    m = _mission()
    try:
        o = m.obj(object_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return m.object_summary(o, min(t_s, m.horizon_s))


@app.get("/api/trajectories")
def trajectories(step_s: float = Query(60.0, ge=10, le=600)):
    """Positions (TEME, km) for every object over the horizon; the client animates by interpolation."""
    m = _mission()
    t = np.arange(0.0, m.horizon_s + 1e-9, step_s)
    out = {}
    for o in m.objects:
        r, _, e = states(o.satrec, m.epoch, t)
        r[e != 0] = np.nan
        out[o.id] = np.round(r, 2).flatten().tolist()
    return {"epoch_utc": m.epoch.iso(), "gmst0_rad": m.epoch.gmst(0.0), "step_s": step_s, "n": len(t), "positions": out}


# ------------------------------------------------------------------ events

@app.get("/api/events")
def events():
    return _mission().events


@app.get("/api/events/{event_id}/separation")
def separation(event_id: str, half_window_s: float = Query(600, ge=30, le=3600), investigation_id: str | None = None, maneuver_id: str | None = None):
    m, ev = _mission(), _event(event_id)
    a, b = m.obj(ev["primary_id"]), m.obj(ev["secondary_id"])
    n = int(min(1201, max(121, half_window_s * 8 + 1)))  # ~0.25 s sampling: crossings run at ~10+ km/s
    result = {"event_id": event_id, "tca_s": ev["tca_s"], "baseline": separation_series(a, b, m.epoch, ev["tca_s"], half_window_s, n=n)}
    if investigation_id and maneuver_id:
        row = db.query_one("SELECT data FROM maneuvers WHERE investigation_id = ? AND id = ?", (investigation_id, maneuver_id))
        if row is None:
            raise HTTPException(404, "maneuver not found")
        mr = row["data"]
        r, v = state(a.satrec, m.epoch, mr["burn_time_s"])
        dv = mnv.direction_vector(mr["direction"], r, v) * (mr["delta_v_m_s"] / 1000)
        off = mnv.make_offset_fn(a, m.epoch, mr["burn_time_s"], dv, m.horizon_s)
        result["maneuvered"] = separation_series(a, b, m.epoch, ev["tca_s"], half_window_s, n=n, offset_fn=off)
        result["maneuver_id"] = maneuver_id
    return result


# ------------------------------------------------------------------ investigations

class StartInvestigation(BaseModel):
    event_id: str = Field(min_length=1, max_length=64)


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    operator: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=500)


@app.post("/api/investigations")
def create_investigation(body: StartInvestigation):
    _event(body.event_id)
    return {"investigation_id": runner.start_investigation(body.event_id, trigger="operator")}


@app.get("/api/investigations/{inv_id}")
def get_investigation(inv_id: str):
    inv = db.query_one("SELECT * FROM investigations WHERE id = ?", (inv_id,))
    if inv is None:
        raise HTTPException(404, "investigation not found")
    inv["steps"] = db.query("SELECT idx, kind, tool, input, output, evidence_id, status, duration_ms, created_at FROM steps WHERE investigation_id = ? ORDER BY idx", (inv_id,))
    inv["maneuvers"] = [r["data"] for r in db.query("SELECT data FROM maneuvers WHERE investigation_id = ? ORDER BY created_at", (inv_id,))]
    return inv


@app.get("/api/investigations/{inv_id}/plan")
def maneuver_plan(inv_id: str, maneuver_id: str | None = None, step_s: float = Query(60.0, ge=10, le=600)):
    """All simulated options (burn points at T−N) and the maneuvered path of the recommended (or given) option."""
    inv = db.query_one("SELECT * FROM investigations WHERE id = ?", (inv_id,))
    if inv is None:
        raise HTTPException(404, "investigation not found")
    try:
        return plan.build_plan(_mission(), inv, maneuver_id, step_s)
    except plan.PlanError as e:
        raise HTTPException(409, str(e))


@app.post("/api/investigations/{inv_id}/decision")
def decide(inv_id: str, body: Decision):
    inv = db.query_one("SELECT status, recommendation FROM investigations WHERE id = ?", (inv_id,))
    if inv is None:
        raise HTTPException(404, "investigation not found")
    if inv["status"] != "awaiting_approval":
        raise HTTPException(409, f"investigation is '{inv['status']}', not awaiting approval")
    decision = {"decision": body.decision, "operator": body.operator.strip(), "note": body.note.strip(), "decided_at": db.now_iso(),
                "recommended_action": (inv["recommendation"] or {}).get("recommended_action"),
                "maneuver_id": (inv["recommendation"] or {}).get("recommended_maneuver_id"),
                "effect": "Decision recorded for audit only. OrbitalGuard cannot command spacecraft."}
    status = "approved" if body.decision == "approve" else "rejected"
    db.execute("UPDATE investigations SET status = ?, decision = ? WHERE id = ?", (status, db.dumps(decision), inv_id))
    db.audit(body.operator.strip(), f"recommendation_{status}", {"investigation": inv_id, **decision})
    return {"investigation_id": inv_id, "status": status, "decision": decision}


# ------------------------------------------------------------------ chat

class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    event_id: str | None = Field(default=None, max_length=64, description="Omit to ask the Mission Director")
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


@app.post("/api/chat")
def chat_endpoint(body: ChatRequest):
    """Ask a Sentinel agent (event_id) or the Mission Director (no event_id). Context is rebuilt server-side."""
    event = _event(body.event_id) if body.event_id else None
    return chat.answer(_mission(), event, body.question.strip(), [t.model_dump() for t in body.history])


def _sse(item: dict) -> str:
    return "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"


@app.post("/api/chat/stream")
def chat_stream_endpoint(body: ChatRequest):
    """Same as /api/chat, but the agent may call analysis tools and every step is streamed (Server-Sent Events)."""
    event = _event(body.event_id) if body.event_id else None
    mission = _mission()
    history = [t.model_dump() for t in body.history]

    def events():
        try:
            for item in chat_stream.stream_answer(mission, event, body.question.strip(), history):
                yield _sse(item)
        except Exception as e:  # never leave the client hanging
            log.exception("chat stream failed")
            yield _sse({"type": "error", "message": f"{type(e).__name__}: {e}"})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/audit")
def audit_log(limit: int = Query(50, ge=1, le=500)):
    return db.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
