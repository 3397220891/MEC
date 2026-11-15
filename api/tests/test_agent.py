"""Agent loop, guardrails and approval workflow. The LLM is a scripted fake that reads tool results,
so the real runner, tool executor, validation and DB code paths are exercised without network."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent import tools as T
from app.llm import ToolCall, ToolTurn


class ScriptedLLM:
    """Plays an investigation like a model would: decides the next call from the conversation so far."""
    providers = [SimpleNamespace(name="fake", model="scripted", vision_model=None)]

    def __init__(self, bad_numbers_first=False):
        self.bad_numbers_first = bad_numbers_first
        self.n = 0

    def _results(self, messages):
        return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]

    def chat_tools(self, messages, tools, use_cache=True, temperature=0.2):
        assert {t["function"]["name"] for t in tools} >= {"simulate_maneuver", "submit_recommendation"}
        self.n += 1
        res = self._results(messages)
        brief = messages[1]["content"]
        event_id = brief.split("ALERT ")[1].split(" ")[0]
        sat = "SIM-OGS2"

        def call(name, **args):
            return ToolTurn(text=f"Calling {name}.", tool_calls=[ToolCall(f"c{self.n}", name, json.dumps(args))],
                            provider="fake", model="scripted", latency_ms=1, cached=False)

        names = [m["tool_calls"][0]["function"]["name"] for m in messages if m.get("tool_calls")]
        if not names:
            return call("get_satellite_info", satellite_id=sat)
        if len(names) == 1:
            return call("analyze_close_approach", satellite_a=sat, satellite_b="SIM-DEB417")
        sims = [r for r in res if r.get("ok") and "maneuver_id" in r.get("result", {})]
        plans = [(95, 0.0), (95, 0.08), (95, -0.12), (48, -0.2)]
        if len(sims) < len(plans):
            lead, dv = plans[len(sims)]
            return call("simulate_maneuver", satellite_id=sat, event_id=event_id, burn_lead_time_min=lead,
                        dv_along_track_m_s=dv)
        ids = [s["result"]["maneuver_id"] for s in sims]
        if "compare_maneuvers" not in names:
            return call("compare_maneuvers", maneuver_ids=ids)
        if "generate_incident_report" not in names:
            return call("generate_incident_report", event_id=event_id)
        cmp_ = next(r for r in res if r.get("ok") and "best_option" in r.get("result", {}))
        best = cmp_["result"]["best_option"]
        best_sim = next(s["result"] for s in sims if s["result"]["maneuver_id"] == best)
        miss = best_sim["new_miss_km"]
        if self.bad_numbers_first and names.count("submit_recommendation") == 0:
            miss = 42.4242  # hallucinated number -> grounding guardrail must reject it
        refs = [r["evidence_ref"] for r in res if r.get("ok")][:3]
        return call("submit_recommendation", decision="maneuver", recommended_maneuver_id=best,
                    summary=f"Recommend {best_sim['label']}, raising miss distance to {miss} km.",
                    rationale=[f"New miss {miss} km with {best_sim['dv_magnitude_m_s']} m/s."],
                    evidence_refs=refs, risks_and_limitations=["No covariance; screening only."],
                    next_steps=["Request CDM."], confidence="medium")


@pytest.fixture
def client():
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def _wait(client, inv_id):
    main.investigations.wait(inv_id, 120)
    return client.get(f"/api/investigations/{inv_id}").json()


def test_llm_agent_full_investigation_and_approval(client):
    main.app.dependency_overrides[main.get_llm] = lambda: ScriptedLLM()
    r = client.post("/api/scenarios/maneuver/load").json()
    assert r["simulated"] and r["auto_investigation_id"]
    inv = _wait(client, r["auto_investigation_id"])
    assert inv["status"] == "awaiting_approval", inv.get("error")
    assert inv["mode"] == "llm"
    rec = inv["recommendation"]
    assert rec["decision"] == "maneuver" and rec["grounding_check"] == "passed"
    # the scripted agent tried the prograde trap; compare must have rejected it for the secondary conjunction
    reasons = [o["rejection_reasons"] for o in inv["report"]["comparison"]["options"]]
    assert any("CUBESAT-CHARLIE" in " ".join(x) for x in reasons)
    assert rec["maneuver"]["dv_rtn_m_s"][1] < 0  # retrograde wins in scenario 3
    tool_steps = [s for s in inv["steps"] if s["kind"] == "tool"]
    assert len(tool_steps) >= 8 and all(s["status"] == "ok" for s in tool_steps)

    bad = client.post(f"/api/investigations/{inv['id']}/decision", json={"decision": "approve", "operator": "x"})
    assert bad.status_code == 422  # operator name too short
    ok = client.post(f"/api/investigations/{inv['id']}/decision",
                     json={"decision": "approve", "operator": "Flight Director", "note": "demo"}).json()
    assert ok["status"] == "approved" and "HYPOTHETICAL" in ok["decision"]["disclaimer"]
    again = client.post(f"/api/investigations/{inv['id']}/decision",
                        json={"decision": "reject", "operator": "Flight Director"})
    assert again.status_code == 409


def test_grounding_guardrail_rejects_invented_numbers(client):
    main.app.dependency_overrides[main.get_llm] = lambda: ScriptedLLM(bad_numbers_first=True)
    r = client.post("/api/scenarios/conjunction/load").json()
    inv = _wait(client, r["auto_investigation_id"])
    subs = [s for s in inv["steps"] if s["name"] == "submit_recommendation"]
    assert subs[0]["status"] == "error" and "GROUNDING" in subs[0]["result"]["error"]
    assert subs[-1]["status"] == "ok" and inv["status"] == "awaiting_approval"


def test_rule_based_fallback_when_no_llm(client):
    main.app.dependency_overrides[main.get_llm] = lambda: SimpleNamespace(providers=[])
    r = client.post("/api/scenarios/maneuver/load").json()
    inv = _wait(client, r["auto_investigation_id"])
    assert inv["mode"] == "rule-based" and inv["status"] == "awaiting_approval", inv.get("error")
    assert inv["recommendation"]["decision"] == "maneuver"
    assert inv["recommendation"]["maneuver"]["secondary_conjunctions"] == [] or all(
        s["status"] not in ("NEW", "worsened") or s["level"] == "WATCH"
        for s in inv["recommendation"]["maneuver"]["secondary_conjunctions"])


def test_tool_errors_are_returned_not_raised(client):
    client.post("/api/scenarios/conjunction/load?autostart=false")
    ctx = T.Context(world=main.world, inv_id="INV-TEST", event=main.world.events[0])
    _, p = T.execute(ctx, "simulate_maneuver", '{"satellite_id": "SIM-OGS2", "event_id": "nope", '
                                               '"burn_lead_time_min": 30, "dv_along_track_m_s": 0.1}')
    assert not p["ok"] and "unknown event_id" in p["error"]
    _, p = T.execute(ctx, "simulate_maneuver", "{not json")
    assert not p["ok"] and "not valid JSON" in p["error"]
    _, p = T.execute(ctx, "no_such_tool", "{}")
    assert not p["ok"]
    _, p = T.execute(ctx, "submit_recommendation", {"decision": "monitor_only", "summary": "x" * 30,
                                                    "rationale": ["r"], "evidence_refs": ["E1"],
                                                    "risks_and_limitations": ["l"], "next_steps": ["n"],
                                                    "confidence": "low"})
    assert not p["ok"] and "generate_incident_report" in p["error"]


def test_dashboard_endpoints(client):
    st = client.post("/api/scenarios/nominal/load").json()
    assert st["events"] == [] and st["auto_investigation_id"] is None
    objs = client.get("/api/objects").json()
    assert len(objs) == 10 and all(o["simulated"] for o in objs)
    tr = client.get("/api/trajectories?duration_min=60&step_s=60").json()
    assert tr["count"] == 61 and len(tr["objects"]["SIM-OGS1"]) == 61 * 3
    assert client.get("/api/objects/NOPE").status_code == 400
    assert client.get("/api/scenarios/unknown/load").status_code == 405
    assert client.post("/api/scenarios/unknown/load").status_code == 404
    assert len(client.get("/api/logs").json()) > 0


def test_chat_tools_cache_replay(tmp_path):
    from app.llm import LLM, Provider

    class FakeOpenAI:
        calls = 0

        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kw):
            FakeOpenAI.calls += 1
            tc = SimpleNamespace(id="call_1", function=SimpleNamespace(name="get_satellite_info",
                                                                       arguments='{"satellite_id": "X"}'))
            msg = SimpleNamespace(content="checking", tool_calls=[tc])
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)

    llm = LLM([Provider("p", "u", "k", "m")], cache_dir=tmp_path / "c", log_path=tmp_path / "l.jsonl",
              client_factory=FakeOpenAI)
    msgs = [{"role": "user", "content": "hi"}]
    a = llm.chat_tools(msgs, T.TOOL_SCHEMAS)
    b = llm.chat_tools(msgs, T.TOOL_SCHEMAS)
    assert a.tool_calls[0].name == "get_satellite_info" and not a.cached
    assert b.cached and b.tool_calls[0].arguments == '{"satellite_id": "X"}' and FakeOpenAI.calls == 1
    assert a.assistant_message()["tool_calls"][0]["function"]["name"] == "get_satellite_info"
