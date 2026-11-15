"""LLM connectivity status for the UI: configured is not the same as reachable.

The result of a real probe (GET /models, short timeout) is cached so the frequent health polls stay cheap.
A connection failure seen by the chat agent marks the LLM unreachable immediately.
"""
from __future__ import annotations

import threading
import time

import openai

from .. import config, db
from ..agent import chat
from ..agent.runner import make_client

PROBE_TTL_S = 60.0
PROBE_TIMEOUT_S = 5.0
_lock = threading.Lock()
_cache: dict = {"at": -1e9, "value": None}


def _result(state: str, detail: str) -> dict:
    return {"state": state, "reachable": state == "online", "model": config.MODEL if config.llm_configured() else None,
            "base_url": config.LLM_BASE_URL, "detail": detail, "checked_at": db.now_iso()}


def llm_status(force: bool = False, client=None) -> dict:
    """state: online | unreachable | auth_error | error | not_configured."""
    if not config.llm_configured():
        return _result("not_configured", "No DEEPSEEK_API_KEY / LLM_API_KEY set, or ORBITALGUARD_AGENT_MODE=offline. "
                                         "The rule-based planner answers instead.")
    if time.monotonic() < chat._llm_down_until:
        return _result("unreachable", "A recent request to the LLM failed to connect; using rule-based answers for now.")
    with _lock:
        if not force and _cache["value"] is not None and time.monotonic() - _cache["at"] < PROBE_TTL_S:
            return _cache["value"]
        try:
            (client or make_client(timeout=PROBE_TIMEOUT_S, max_retries=0)).models.list()
            value = _result("online", f"{config.MODEL} reachable at {config.LLM_BASE_URL}")
        except openai.AuthenticationError as e:
            value = _result("auth_error", f"The LLM rejected the API key (HTTP {e.status_code}). Check DEEPSEEK_API_KEY.")
        except openai.APIConnectionError:
            value = _result("unreachable", f"Cannot connect to {config.LLM_BASE_URL} (network blocked, offline or timeout). "
                                           "Investigations and chat fall back to the rule-based planner.")
        except openai.APIStatusError as e:
            value = _result("error", f"The LLM returned HTTP {e.status_code}.")
        _cache.update(at=time.monotonic(), value=value)
        return value
