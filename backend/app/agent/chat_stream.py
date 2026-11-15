"""Streaming agent chat with live tool use ("what-if" analysis computed on demand).

The LLM can call the same deterministic analysis tools as the investigation agent. Everything is
streamed to the operator as Server-Sent Events while it happens:

  start        {agent, mode, model}
  thought      {delta}                      model reasoning (DeepSeek thinking models)
  text         {delta}                      model text: narration before tool calls, or the answer
  tool_call    {id, name, input}            the model asked for a tool
  tool_result  {id, name, ok, evidence_id, summary, maneuver_id?}   computed by project code
  notice       {message}                    e.g. falling back to deterministic answers
  final        {answer, evidence, check, mode, model, tool_calls, reason?}

Numbers come only from the tools. New evidence and maneuvers continue the investigation's numbering
(E16, M10, ...) and maneuvers are stored with the investigation so the globe can draw them.
Streaming costs the same tokens as a normal call; it only changes when they arrive.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import openai

from .. import config, db
from . import chat
from .runner import OPENAI_TOOLS, make_client
from .tools import InvestigationContext, run_tool

CHAT_TOOL_NAMES = {"get_satellite_info", "get_trajectory", "analyze_close_approach", "simulate_maneuver", "compare_maneuvers"}
CHAT_TOOLS = [t for t in OPENAI_TOOLS if t["function"]["name"] in CHAT_TOOL_NAMES]
MAX_TOOL_CALLS = 8
MAX_ROUNDS = 6
WALL_CLOCK_S = 150
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


@dataclass
class ChatContext(InvestigationContext):
    """Tool context for chat: seeded with the investigation's evidence/maneuvers; logs nothing to the timeline."""

    def log(self, *args, **kwargs) -> None:  # tool steps are streamed to the chat instead
        return None


def _session_lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _seed_context(mission, event: dict) -> ChatContext:
    inv = chat._latest_investigation(event["id"], finished=True)
    ctx = ChatContext(id=inv["id"] if inv else f"CHAT-{event['id']}", mission=mission, event=event)
    if inv:
        for r in db.query("SELECT evidence_id, tool, input, output FROM steps WHERE investigation_id = ? AND evidence_id IS NOT NULL ORDER BY idx", (inv["id"],)):
            ctx.evidence[r["evidence_id"]] = {"tool": r["tool"], "input": {k: v for k, v in (r["input"] or {}).items() if k != "actor"}, "output": r["output"]}
        for r in db.query("SELECT id, data FROM maneuvers WHERE investigation_id = ?", (inv["id"],)):
            ctx.maneuvers[r["id"]] = r["data"]
    return ctx


def summarize(name: str, out: dict) -> str:
    """One line per tool result for the chat transcript (values copied from the tool output)."""
    if "error" in out:
        return f"error: {out['error']}"
    if name == "simulate_maneuver":
        return (f"{out['maneuver_id']}: {out['direction']} {out['delta_v_m_s']} m/s at T−{out['burn_lead_time_min']} min → "
                f"new miss {out['new_miss_km']} km at {out['new_tca_utc'][11:19]}Z, "
                f"{len(out.get('secondary_conjunctions') or [])} new close approach(es)")
    if name == "compare_maneuvers":
        best = out.get("best_option")
        return f"{len(out.get('options', [])) - 1} options vs target {out.get('target_miss_km')} km → best {best or 'none meets the target'}"
    if name == "analyze_close_approach":
        a = (out.get("approaches_within_50km") or [{}])
        m = min(a, key=lambda x: x.get("miss_km", 1e9)) if a else {}
        return f"closest {m.get('miss_km')} km at {str(m.get('tca_utc', ''))[11:19]}Z, combined 1σ {m.get('combined_position_sigma_km')} km"
    if name == "get_satellite_info":
        return f"{out.get('name')} · {out.get('kind')} · maneuverable={out.get('maneuverable')} · TLE age {out.get('tle_age_days_at_scenario_epoch')} d"
    if name == "get_trajectory":
        s = out.get("summary") or {}
        return "trajectory " + ", ".join(f"{k}={v}" for k, v in list(s.items())[:4])
    return "ok"


SYSTEM = """You are {agent}, an analysis agent in OrbitalGuard, an advisory collision-risk assistant for a \
human satellite operator. You answer questions about one close-approach event.

You have the investigation's evidence below, and you can call analysis tools to compute new results \
(e.g. what-if maneuvers). The tools run the project's own physics code (SGP4 + J2 maneuver model, \
re-screening against the catalog).

Rules:
- Every number you state must come from the CONTEXT or from a tool result. Never compute or estimate \
distances, times, delta-v or probabilities yourself: call a tool instead.
- For a what-if question, call simulate_maneuver (and compare_maneuvers if useful), then answer.
- Cite evidence IDs inline like (E16). Existing maneuvers can be referenced by their IDs (M1...).
- At most {max_calls} tool calls. Before calling tools, say in one short sentence what you will check.
- No collision probability (no covariance); do not call a maneuver "safe". Advisory only: nothing is executed.
- Final answer: 2-5 sentences, plain text (no JSON, no markdown tables), in the operator's language.

CONTEXT:
{context}"""

SYSTEM_DIRECTOR = """You are the Mission Director in OrbitalGuard, an advisory collision-risk assistant. You \
coordinate all active alerts: help the operator decide which alert to review first and summarise their status. \
Use only numbers from the CONTEXT, refer to alerts by event id, no collision probability, advisory only. \
Answer in 2-5 sentences of plain text, in the operator's language.

CONTEXT:
{context}"""


def _sse_fallback(mission, event, question: str, reason: str) -> Iterator[dict]:
    ctx, evidence = chat.build_context(mission, event)
    out = chat._fallback_answer(event, ctx, evidence, question)
    yield {"type": "notice", "message": f"Deterministic answer ({reason})"}
    words = re.split(r"(\s+)", out["answer"])
    for i in range(0, len(words), 6):  # stream rule-based text in small chunks for a consistent UI
        yield {"type": "text", "delta": "".join(words[i:i + 6])}
        time.sleep(0.02)
    yield {"type": "final", "answer": out["answer"], "evidence": out["evidence"], "mode": "fallback", "model": "deterministic",
           "reason": reason, "tool_calls": 0, "check": chat.check_answer(out["answer"], out["evidence"], ctx, evidence)}


def stream_answer(mission, event: dict | None, question: str, history: list[dict], client=None) -> Iterator[dict]:
    agent = chat.agent_name(event["id"] if event else None)
    use_llm = config.llm_configured() or client is not None
    reason = None if use_llm else "LLM not configured"
    if use_llm and client is None and time.monotonic() < chat._llm_down_until:
        use_llm, reason = False, "LLM recently unreachable"
    yield {"type": "start", "agent": agent, "event_id": event["id"] if event else None,
           "mode": "llm" if use_llm else "fallback", "model": config.MODEL if use_llm else "deterministic"}
    if not use_llm:
        yield from _sse_fallback(mission, event, question, reason)
        return

    ctx_view, _ = chat.build_context(mission, event)
    tool_ctx = _seed_context(mission, event) if event else None
    system = (SYSTEM.format(agent=agent, max_calls=MAX_TOOL_CALLS, context=json.dumps(ctx_view, ensure_ascii=False)) if event
              else SYSTEM_DIRECTOR.format(context=json.dumps(ctx_view, ensure_ascii=False)))
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages += [{"role": t["role"], "content": t["content"]} for t in history[-chat.MAX_HISTORY_TURNS:]]
    messages.append({"role": "user", "content": question})

    lock = _session_lock(tool_ctx.id) if tool_ctx else None
    if lock:
        lock.acquire()
    try:
        client = client or make_client(timeout=config.CHAT_TIMEOUT_S, max_retries=0)
        deadline = time.monotonic() + WALL_CLOCK_S
        n_calls, closed, answer = 0, False, ""
        for _round in range(MAX_ROUNDS):
            kwargs: dict[str, Any] = {"model": config.MODEL, "messages": messages, "stream": True, "max_tokens": 4000}
            if tool_ctx is not None:
                kwargs.update(tools=CHAT_TOOLS, tool_choice="none" if closed else "auto")
            stream = client.chat.completions.create(**kwargs)
            content, reasoning, calls = "", "", {}
            for chunk in stream:
                if not chunk.choices:
                    continue
                d = chunk.choices[0].delta
                rc = getattr(d, "reasoning_content", None) or (getattr(d, "model_extra", None) or {}).get("reasoning_content")
                if rc:
                    reasoning += rc
                    yield {"type": "thought", "delta": rc}
                if d.content:
                    content += d.content
                    yield {"type": "text", "delta": d.content}
                for tc in d.tool_calls or []:
                    c = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    c["id"] = tc.id or c["id"]
                    if tc.function and tc.function.name:
                        c["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        c["arguments"] += tc.function.arguments
            msg: dict[str, Any] = {"role": "assistant", "content": content or None}
            if reasoning:
                msg["reasoning_content"] = reasoning  # DeepSeek expects it back within a tool loop
            if calls:
                msg["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                                     for c in calls.values()]
            messages.append(msg)
            if not calls:
                answer = content.strip()
                break
            for c in calls.values():
                try:
                    args = json.loads(c["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = None
                yield {"type": "tool_call", "id": c["id"], "name": c["name"], "input": args}
                n_calls += 1
                if n_calls > MAX_TOOL_CALLS or time.monotonic() > deadline:
                    payload, eid, ok = {"error": "tool budget exhausted; answer now with the results you have"}, None, False
                    closed = True
                elif c["name"] not in CHAT_TOOL_NAMES or not isinstance(args, dict):
                    payload, eid, ok = {"error": f"unknown tool or invalid arguments for '{c['name']}'"}, None, False
                else:
                    payload, eid, is_err = run_tool(tool_ctx, c["name"], args, "chat-agent")
                    ok = not is_err
                res = {"type": "tool_result", "id": c["id"], "name": c["name"], "ok": ok, "evidence_id": eid,
                       "summary": summarize(c["name"], payload)}
                if ok and c["name"] == "simulate_maneuver":
                    res["maneuver_id"] = payload.get("maneuver_id")
                    res["investigation_id"] = tool_ctx.id
                yield res
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(payload)})
        if not answer:
            raise ValueError("no final answer within the round limit")
    except openai.APIConnectionError:
        with chat._down_lock:
            chat._llm_down_until = time.monotonic() + config.CHAT_LLM_BACKOFF_S
        yield from _sse_fallback(mission, event, question, "LLM unreachable (connection error/timeout)")
        return
    except openai.APIStatusError as e:
        yield from _sse_fallback(mission, event, question, f"LLM API error {e.status_code}")
        return
    except (ValueError, KeyError, TypeError) as e:
        yield from _sse_fallback(mission, event, question, f"LLM reply unusable: {e}")
        return
    finally:
        if lock:
            lock.release()

    cited = sorted(set(re.findall(r"\bE\d+\b", answer)), key=lambda e: int(e[1:]))
    evidence = tool_ctx.evidence if tool_ctx else {}
    check = chat.check_answer(answer, cited, ctx_view, evidence)
    db.audit("operator", "chat", {"agent": agent, "event": event["id"] if event else None, "question": question[:500],
                                  "mode": "llm", "tool_calls": n_calls, "grounded": check["grounded"]})
    yield {"type": "final", "answer": answer, "evidence": cited, "mode": "llm", "model": config.MODEL,
           "tool_calls": n_calls, "check": check}
