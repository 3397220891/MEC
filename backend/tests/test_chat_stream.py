"""Streaming chat with live tool use, against a fake streaming OpenAI-compatible client."""
import json
from types import SimpleNamespace as NS

import pytest

from app import db
from app.agent import chat, chat_stream, runner
from app.agent.offline import run_offline
from app.agent.tools import InvestigationContext
from app.services import plan
from app.services.state import MissionStore


@pytest.fixture()
def mission(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "stream.db")
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.setattr(chat, "_llm_down_until", 0.0)
    m = MissionStore().load("maneuver")
    ev = min(m.events, key=lambda e: e["miss_km"])
    db.execute("INSERT INTO investigations (id, event_id, scenario_id, status, mode, model, started_at) VALUES (?,?,?,?,?,?,?)",
               ("INV-S", ev["id"], m.scenario_id, "running", "fallback", "deterministic-planner", db.now_iso()))
    ctx = InvestigationContext(id="INV-S", mission=m, event=ev)
    runner._finalize(ctx, run_offline(ctx, "test"), "fallback")
    return m, ev, len(ctx.evidence), len(ctx.maneuvers)


def chunk(content=None, reasoning=None, tool=None):
    tcs = [NS(index=0, id=tool[0], function=NS(name=tool[1], arguments=tool[2]))] if tool else None
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tcs, reasoning_content=reasoning, model_extra={}), finish_reason=None)])


class FakeStreamClient:
    def __init__(self, rounds):
        self.rounds, self.requests = list(rounds), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        assert kw["stream"] is True
        self.requests.append(json.loads(json.dumps(kw["messages"], default=str)))
        r = self.rounds.pop(0)
        return iter(r(self.requests[-1]) if callable(r) else r)


def test_what_if_is_computed_by_tools_and_streamed(mission):
    m, ev, n_ev, n_man = mission
    args = json.dumps({"satellite_id": ev["primary_id"], "secondary_id": ev["secondary_id"],
                       "burn_lead_time_min": 100, "direction": "prograde", "delta_v_m_s": 0.3})
    new_e, new_m = f"E{n_ev + 1}", f"M{n_man + 1}"

    def answer(messages):
        result = json.loads(messages[-1]["content"])  # the tool result the model received
        return [chunk(content=f"{new_m} gives a predicted miss of {result['new_miss_km']} km "),
                chunk(content=f"({new_e}).")]

    client = FakeStreamClient([
        [chunk(reasoning="Need a later burn."), chunk(content="I'll simulate a burn 100 min before TCA."),
         chunk(tool=("call_1", "simulate_maneuver", args[:20])), chunk(tool=("", None, args[20:]))],
        answer,
    ])
    events = list(chat_stream.stream_answer(m, ev, "What if we burn 100 min before TCA?", [], client=client))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "final"
    assert "thought" in kinds and kinds.index("tool_call") < kinds.index("tool_result")
    call = next(e for e in events if e["type"] == "tool_call")
    assert call["name"] == "simulate_maneuver" and call["input"]["burn_lead_time_min"] == 100
    res = next(e for e in events if e["type"] == "tool_result")
    assert res["ok"] and res["evidence_id"] == new_e and res["maneuver_id"] == new_m and "T−100" in res["summary"]
    final = events[-1]
    assert final["mode"] == "llm" and final["tool_calls"] == 1 and final["evidence"] == [new_e]
    assert final["check"]["grounded"], final["check"]
    # the streamed text pieces add up to the final answer
    assert "".join(e["delta"] for e in events[kinds.index("tool_result"):] if e["type"] == "text").strip() == final["answer"]
    # the new maneuver belongs to the investigation, so the globe can draw it
    p = plan.build_plan(m, db.query_one("SELECT * FROM investigations WHERE id = 'INV-S'"), maneuver_id=new_m)
    assert p["path"]["maneuver_id"] == new_m
    # the assistant turn sent back to the model keeps the tool call and the reasoning
    second = client.requests[1]
    assert second[-2]["tool_calls"][0]["function"]["arguments"] == args and second[-2]["reasoning_content"]


def test_stream_falls_back_without_llm(mission, monkeypatch):
    m, ev, *_ = mission
    monkeypatch.setattr(chat_stream.config, "llm_configured", lambda: False)
    events = list(chat_stream.stream_answer(m, ev, "为什么是红色？", []))
    assert events[0]["mode"] == "fallback" and events[-1]["mode"] == "fallback"
    assert "".join(e["delta"] for e in events if e["type"] == "text") == events[-1]["answer"]
