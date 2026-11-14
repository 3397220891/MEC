"""用假的 OpenAI 客户端测试调用层，不需要网络和 key。运行：pytest -q"""
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APIConnectionError
from pydantic import BaseModel

from app.llm import LLM, LLMError, Provider
from app import main


class FakeClient:
    """按顺序返回预设回答；回答是 Exception 就抛出。记录每次请求。"""
    scripts: dict[str, list] = {}
    calls: list[dict] = []

    def __init__(self, base_url, api_key, timeout, max_retries):
        self.name = base_url
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        FakeClient.calls.append({"provider": self.name, **kwargs})
        item = FakeClient.scripts[self.name].pop(0)
        if isinstance(item, Exception):
            raise item
        usage = SimpleNamespace(model_dump=lambda: {"prompt_tokens": 10, "completion_tokens": 5})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=item))], usage=usage)


def conn_error():
    return APIConnectionError(request=httpx.Request("POST", "http://x"))


@pytest.fixture
def make_llm(tmp_path, monkeypatch):
    monkeypatch.setattr("app.llm.time.sleep", lambda s: None)  # 测试里不真等
    FakeClient.calls = []

    def _make(scripts, vision=("v-model", None)):
        FakeClient.scripts = scripts
        providers = [
            Provider("primary", "primary", "k", "p-model", vision_model=vision[0]),
            Provider("backup", "backup", "k", "b-model", vision_model=vision[1]),
        ]
        return LLM(providers, cache_dir=tmp_path / "cache", log_path=tmp_path / "log.jsonl",
                   client_factory=FakeClient, max_retries=1)
    return _make


def test_cache_hit_skips_second_call(make_llm):
    llm = make_llm({"primary": ["hello"], "backup": []})
    a = llm.chat("hi")
    b = llm.chat("hi")
    assert (a.text, a.cached) == ("hello", False)
    assert (b.text, b.cached) == ("hello", True)
    assert len(FakeClient.calls) == 1


def test_falls_back_after_retries(make_llm):
    llm = make_llm({"primary": [conn_error(), conn_error()], "backup": ["from backup"]})
    r = llm.chat("hi")
    assert r.provider == "backup" and r.text == "from backup"
    assert [c["provider"] for c in FakeClient.calls] == ["primary", "primary", "backup"]


def test_all_fail_raises(make_llm):
    llm = make_llm({"primary": [conn_error(), conn_error()], "backup": [conn_error(), conn_error()]})
    with pytest.raises(LLMError):
        llm.chat("hi")


class Answer(BaseModel):
    value: int


def test_schema_repair_loop(make_llm):
    llm = make_llm({"primary": ["not json", '```json\n{"value": 42}\n```'], "backup": []})
    r = llm.chat("give a number", schema=Answer)
    assert r.parsed.value == 42 and r.attempts == 2
    assert FakeClient.calls[0]["response_format"] == {"type": "json_object"}
    # 第二次请求里带上了上一轮的错误回答和修复提示
    assert "rejected" in FakeClient.calls[1]["messages"][-1]["content"]


def test_verify_hook_triggers_repair(make_llm):
    llm = make_llm({"primary": ['{"value": -1}', '{"value": 7}'], "backup": []})
    r = llm.chat("positive number", schema=Answer,
                 verify=lambda a: None if a.value > 0 else "value must be positive")
    assert r.parsed.value == 7


def test_vision_uses_vision_model_and_skips_providers_without_one(make_llm):
    llm = make_llm({"primary": [conn_error(), conn_error()], "backup": []}, vision=("v-model", None))
    with pytest.raises(LLMError, match="no vision model"):
        llm.chat("what is this", images=[b"\x89PNG fake"])
    assert FakeClient.calls[0]["model"] == "v-model"
    content = FakeClient.calls[0]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_api_endpoints(make_llm):
    llm = make_llm({"primary": ["pong", '{"goal": "g", "steps": ["a", "b"]}'], "backup": []})
    main.app.dependency_overrides[main.get_llm] = lambda: llm
    try:
        c = TestClient(main.app)
        assert c.get("/api/health").json()["providers"][0]["name"] == "primary"
        assert c.post("/api/chat", json={"prompt": "ping"}).json()["text"] == "pong"
        r = c.post("/api/breakdown", json={"prompt": "do x"}).json()
        assert r["result"]["steps"] == ["a", "b"]
    finally:
        main.app.dependency_overrides.clear()
