"""LLM agent loop against a fake OpenAI-compatible client (no network, no key)."""
import json
from types import SimpleNamespace as NS

import pytest

from app import db
from app.agent import runner
from app.agent.tools import InvestigationContext
from app.services.state import MissionStore


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "_conn", None)
    mission = MissionStore().load("maneuver")
    ev = min(mission.events, key=lambda e: e["miss_km"])
    return InvestigationContext(id="INV-T", mission=mission, event=ev)


def _msg(content=None, tool_calls=None, finish="stop", reasoning=None):
    m = NS(content=content, tool_calls=tool_calls, reasoning_content=reasoning, model_extra={})
    m.model_dump = lambda exclude_none=True: {"role": "assistant", "content": content,
                                              **({"tool_calls": [{"id": c.id, "type": "function", "function": {
                                                  "name": c.function.name, "arguments": c.function.arguments}}
                                                  for c in tool_calls]} if tool_calls else {})}
    return NS(choices=[NS(message=m, finish_reason=finish)])


def _call(i, name, args):
    return NS(id=f"call_{i}", function=NS(name=name, arguments=json.dumps(args)))


class FakeClient:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        return self.replies.pop(0)(kw) if callable(self.replies[0]) else self.replies.pop(0)


def test_tool_loop_produces_valid_recommendation(ctx):
    ev = ctx.event

    def final(kw):
        tool_msgs = [json.loads(m["content"]) for m in kw["messages"] if m["role"] == "tool"]
        eid = tool_msgs[0]["evidence_id"]
        rec = {"headline": "Close approach under investigation", "risk_level": "HIGH",
               "recommended_action": "MONITOR_AND_REQUEST_TRACKING", "recommended_maneuver_id": "",
               "rationale": [{"claim": "The primary object's metadata was retrieved.", "evidence": [eid]}],
               "uncertainties": ["No covariance."], "next_steps": ["Request tracking."]}
        return _msg(content="```json\n" + json.dumps(rec) + "\n```", reasoning="thinking...")

    client = FakeClient([
        _msg(tool_calls=[_call(1, "get_satellite_info", {"satellite_id": ev["primary_id"]}),
                         _call(2, "get_satellite_info", {"satellite_id": ev["secondary_id"]})], finish="tool_calls"),
        final,
    ])
    rec = runner._run_llm(ctx, client)
    assert rec["recommended_action"] == "MONITOR_AND_REQUEST_TRACKING"
    assert len(ctx.evidence) == 2
    first = client.requests[0]
    assert first["messages"][0]["role"] == "system"
    assert {t["function"]["name"] for t in first["tools"]} >= {"get_satellite_info", "simulate_maneuver"}


def test_bad_answer_twice_falls_back(ctx):
    client = FakeClient([_msg(content="not json"), _msg(content="still not json")])
    with pytest.raises(runner._FallbackNeeded):
        runner._run_llm(ctx, client)


def test_short_utc_times_are_not_checked_as_numbers():
    from app.agent.report import _NUM, _TIMESTAMP

    text = _TIMESTAMP.sub(" ", "22.55 km at 07:29Z, TCA 09:04:59Z, epoch 2026-11-15T06:00:00Z, burn at 14:05 UTC")
    assert [m.group(1) for m in _NUM.finditer(text)] == ["22.55"]
