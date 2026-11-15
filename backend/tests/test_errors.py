"""Error handling: LLM reachability status and readable errors for unexpected failures."""
from types import SimpleNamespace as NS

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent import chat
from app.services import health


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(health, "_cache", {"at": -1e9, "value": None})
    monkeypatch.setattr(chat, "_llm_down_until", 0.0)
    monkeypatch.setattr(health.config, "llm_configured", lambda: True)


def client_raising(exc=None):
    def list_models():
        if exc:
            raise exc
        return []
    return NS(models=NS(list=list_models))


REQ = httpx.Request("GET", "https://llm.invalid/models")


def test_not_configured(monkeypatch):
    monkeypatch.setattr(health.config, "llm_configured", lambda: False)
    s = health.llm_status()
    assert s["state"] == "not_configured" and not s["reachable"]


def test_online_and_cached():
    assert health.llm_status(client=client_raising())["state"] == "online"
    # cached: a failing client is not consulted within the TTL
    assert health.llm_status(client=client_raising(openai.APIConnectionError(request=REQ)))["state"] == "online"


def test_unreachable_and_bad_key():
    s = health.llm_status(force=True, client=client_raising(openai.APIConnectionError(request=REQ)))
    assert s["state"] == "unreachable" and "rule-based" in s["detail"]
    bad = openai.AuthenticationError("bad key", response=httpx.Response(401, request=REQ), body=None)
    assert health.llm_status(force=True, client=client_raising(bad))["state"] == "auth_error"


def test_recent_chat_failure_marks_unreachable(monkeypatch):
    monkeypatch.setattr(chat, "_llm_down_until", 1e12)
    assert health.llm_status(client=client_raising())["state"] == "unreachable"


def test_unhandled_error_returns_readable_json():
    def boom():
        raise RuntimeError("kaboom")
    main.app.add_api_route("/api/_test_boom", boom)
    try:
        r = TestClient(main.app, raise_server_exceptions=False).get("/api/_test_boom")
    finally:
        main.app.router.routes = [rt for rt in main.app.router.routes if getattr(rt, "path", "") != "/api/_test_boom"]
    assert r.status_code == 500
    body = r.json()
    assert body["error"] == "internal_error" and body["reference"] in body["detail"] and "RuntimeError" in body["detail"]
