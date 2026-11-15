"""Agent chat: context building, grounding checks and the deterministic fallback (no network)."""
import json
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from app import db
from app.agent import chat, runner
from app.agent.offline import run_offline
from app.agent.tools import InvestigationContext
from app.services.state import MissionStore


@pytest.fixture()
def mission(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "chat.db")
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.setattr(chat, "_llm_down_until", 0.0)
    m = MissionStore().load("maneuver")
    for n, ev in enumerate(e for e in m.events if e["severity"] in ("RED", "AMBER")):
        inv_id = f"INV-T{n}"
        db.execute("INSERT INTO investigations (id, event_id, scenario_id, status, mode, model, started_at) VALUES (?,?,?,?,?,?,?)",
                   (inv_id, ev["id"], m.scenario_id, "running", "fallback", "deterministic-planner", db.now_iso()))
        ctx = InvestigationContext(id=inv_id, mission=m, event=ev)
        runner._finalize(ctx, run_offline(ctx, "test"), "fallback")
    return m


def red_event(m):
    return next(e for e in m.events if e["severity"] == "RED")


class FakeClient:
    def __init__(self, reply=None, exc=None):
        self.reply, self.exc, self.requests = reply, exc, []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        if self.exc:
            raise self.exc
        return NS(choices=[NS(message=NS(content=json.dumps(self.reply)), finish_reason="stop")])


def test_context_contains_investigation_evidence(mission):
    ctx, evidence = chat.build_context(mission, red_event(mission))
    assert ctx["event"]["severity"] == "RED"
    assert ctx["investigation"]["recommendation"]["recommended_action"]
    assert evidence and all(k.startswith("E") for k in evidence)
    assert {e["tool"] for e in evidence.values()} >= {"analyze_close_approach", "compare_maneuvers"}


def test_llm_answer_is_sent_with_context_and_history_and_checked(mission):
    ev = red_event(mission)
    client = FakeClient({"answer": f"The predicted miss is {ev['miss_km']} km; a made-up figure is 7777.7 km.", "evidence": ["E3", "E99"]})
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    out = chat.answer(mission, ev, "Why is this RED?", history, client=client)
    assert out["mode"] == "llm" and out["agent"] == "Sentinel MAN-001"
    msgs = client.requests[0]["messages"]
    assert msgs[0]["role"] == "system" and '"evidence"' in msgs[0]["content"]  # context injected every call
    assert [m["content"] for m in msgs[1:]] == ["hi", json.dumps({"answer": "hello", "evidence": []}), "Why is this RED?"]
    assert out["check"]["unknown_evidence"] == ["E99"]
    assert out["check"]["unverified_numbers"] == ["7777.7"]
    assert out["check"]["grounded"] is False


def test_connection_error_falls_back_and_backs_off(mission):
    ev = red_event(mission)
    err = openai.APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
    out = chat.answer(mission, ev, "哪个机动方案最好？", [], client=FakeClient(exc=err))
    assert out["mode"] == "fallback" and "unreachable" in out["reason"]
    assert "Option" in out["answer"] and out["check"]["grounded"]
    assert chat._llm_down_until > 0


def test_director_prioritises_red_alert_without_llm(mission, monkeypatch):
    monkeypatch.setattr(chat.config, "llm_configured", lambda: False)
    out = chat.answer(mission, None, "Which alert first?", [])
    assert out["agent"] == "Mission Director" and out["mode"] == "fallback"
    assert out["answer"].startswith(f"Review {red_event(mission)['id']} first")


def test_prose_reply_is_accepted_and_history_kept_as_json(mission):
    ev = red_event(mission)
    client = FakeClient()
    client.chat.completions.create = lambda **kw: (client.requests.append(kw), NS(choices=[NS(
        message=NS(content=f"Miss is {ev['miss_km']} km (E3)."), finish_reason="stop")]))[1]
    out = chat.answer(mission, ev, "and then?", [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}], client=client)
    assert out["mode"] == "llm" and out["evidence"] == ["E3"]
    assert json.loads(client.requests[0]["messages"][2]["content"]) == {"answer": "a", "evidence": []}


def test_running_investigation_does_not_hide_finished_one(mission):
    ev = red_event(mission)
    db.execute("INSERT INTO investigations (id, event_id, scenario_id, status, mode, model, started_at) VALUES (?,?,?,?,?,?,?)",
               ("INV-NEW", ev["id"], mission.scenario_id, "running", "llm", "x", "9999-01-01T00:00:00Z"))
    ctx, evidence = chat.build_context(mission, ev)
    assert ctx["newer_investigation_in_progress"]["id"] == "INV-NEW"
    assert ctx["investigation"]["id"] == "INV-T0" and evidence


def test_numbers_restated_from_validated_recommendation_text_are_grounded(mission):
    ctx, evidence = chat.build_context(mission, None)
    headline = "a 0.20 m/s prograde burn (M9) raises the predicted miss to 3.48 km"
    ctx["alerts"][0]["headline"] = headline
    assert chat.check_answer("Option M9 is 0.20 m/s for 3.48 km.", [], ctx, evidence)["grounded"]
    assert chat.check_answer("Option M9 is 0.20 m/s for 7777.7 km.", [], ctx, evidence)["unverified_numbers"] == ["7777.7"]
