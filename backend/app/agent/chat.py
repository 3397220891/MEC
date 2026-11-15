"""Operator Q&A ("agent group chat") grounded in the investigation evidence.

Every request is stateless: the server rebuilds the context (event, thresholds, latest
investigation, evidence, maneuver comparison) from the database on each call and sends it
together with the recent chat history. The model may only use numbers and evidence IDs from
that context; answers are checked the same way as investigation reports.

Two kinds of agent answer:
  - a per-event "Sentinel" agent, grounded in that event's latest investigation;
  - the "Mission Director", which sees a summary of every RED/AMBER alert and helps prioritise.

Without an LLM (no key, network blocked, error) a deterministic answer is built from the same data.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

import openai

from .. import config, db
from .report import _NUM, _TIMESTAMP, _leaves, _matches
from .runner import _parse_json, make_client

MAX_HISTORY_TURNS = 8
MAX_EVIDENCE_CHARS = 5000
# Bulky arrays and boilerplate text repeated in every tool output.
_DROP_KEYS = {"points", "samples", "trajectory", "positions", "series", "assumptions", "limitations", "ranking_rule", "notes"}

_down_lock = threading.Lock()
_llm_down_until = 0.0  # after a connection failure, skip the LLM for a while (keeps the demo snappy offline)


def agent_name(event_id: str | None) -> str:
    return f"Sentinel {event_id.removeprefix('EVT-')}" if event_id else "Mission Director"


# ------------------------------------------------------------------ context

def _compact(x: Any, depth: int = 0) -> Any:
    """Drop bulky sample arrays so the evidence fits in the prompt."""
    if isinstance(x, dict):
        return {k: _compact(v, depth + 1) for k, v in x.items() if k not in _DROP_KEYS}
    if isinstance(x, list):
        return [_compact(v, depth + 1) for v in x[:12]]
    if isinstance(x, float):
        return round(x, 4)
    return x


_DONE = ("awaiting_approval", "approved", "rejected")


def _latest_investigation(event_id: str, finished: bool = False) -> dict | None:
    if finished:
        return db.query_one("SELECT * FROM investigations WHERE event_id = ? AND status IN (?,?,?) ORDER BY started_at DESC LIMIT 1",
                            (event_id, *_DONE))
    return db.query_one("SELECT * FROM investigations WHERE event_id = ? ORDER BY started_at DESC LIMIT 1", (event_id,))


def _evidence(inv_id: str) -> dict[str, dict]:
    rows = db.query("SELECT evidence_id, tool, input, output FROM steps WHERE investigation_id = ? AND evidence_id IS NOT NULL ORDER BY idx", (inv_id,))
    out = {}
    for r in rows:
        inp = {k: v for k, v in (r["input"] or {}).items() if k != "actor"}
        item = {"tool": r["tool"], "input": inp, "output": _compact(r["output"])}
        text = json.dumps(item)
        if len(text) > MAX_EVIDENCE_CHARS:  # keep it valid JSON but bounded
            item["output"] = {k: v for k, v in item["output"].items() if not isinstance(v, (list, dict))} if isinstance(item["output"], dict) else None
            item["truncated"] = True
        out[r["evidence_id"]] = item
    return out


def _event_brief(ev: dict) -> dict:
    keys = ("id", "primary_name", "secondary_name", "tca_utc", "miss_km", "rel_speed_km_s", "miss_ric_km", "severity", "data_label")
    return {k: ev[k] for k in keys if k in ev}


def build_context(mission, event: dict | None) -> tuple[dict, dict]:
    """Return (context sent to the model, evidence map used for citation checks)."""
    thresholds = {"red_km": config.RED_KM, "amber_km": config.AMBER_KM, "screen_km": config.SCREEN_THRESHOLD_KM}
    ctx: dict[str, Any] = {"scenario": mission.scenario_id, "data_source": mission.data_info.get("source"),
                           "scenario_epoch_utc": mission.epoch.iso(), "thresholds": thresholds}
    evidence: dict[str, dict] = {}
    if event is None:  # Mission Director: summary of every alert
        alerts = []
        for ev in mission.events:
            if ev["severity"] not in ("RED", "AMBER"):
                continue
            inv = _latest_investigation(ev["id"]) or {}
            rec = inv.get("recommendation") or {}
            alerts.append({**_event_brief(ev), "investigation": inv.get("id"), "status": inv.get("status"),
                           "recommended_action": rec.get("recommended_action"), "recommended_maneuver_id": rec.get("recommended_maneuver_id"),
                           "headline": rec.get("headline")})
        ctx["alerts"] = alerts
        return ctx, evidence
    ctx["event"] = _event_brief(event)
    # Ground answers in the latest *finished* investigation; mention a newer one that is still running.
    latest = _latest_investigation(event["id"])
    inv = _latest_investigation(event["id"], finished=True)
    if latest and latest["status"] not in _DONE:
        ctx["newer_investigation_in_progress"] = {"id": latest["id"], "status": latest["status"]}
    if inv:
        evidence = _evidence(inv["id"])
        ctx["investigation"] = {"id": inv["id"], "status": inv["status"], "mode": inv["mode"],
                                "recommendation": inv.get("recommendation"), "decision": inv.get("decision")}
        ctx["evidence"] = evidence
    return ctx, evidence


# ------------------------------------------------------------------ checks

def check_answer(answer: str, cited: list[str], ctx: dict, evidence: dict) -> dict:
    unknown = [c for c in cited if c not in evidence]
    # Like the report validator: numbers must come from the event, the thresholds, the alert list, or the
    # evidence the answer actually cites. Matching against the whole context would accept almost anything.
    source = {"event": ctx.get("event"), "thresholds": ctx.get("thresholds"), "alerts": ctx.get("alerts"),
              "cited": [evidence[c]["output"] for c in cited if c in evidence]}
    leaves = _leaves(source, [])
    unverified = []
    for m in _NUM.finditer(_TIMESTAMP.sub(" ", answer)):
        txt = m.group(1)
        dec = len(txt.split(".")[1]) if "." in txt else 0
        if dec == 0 and float(txt) < 10:
            continue
        if not _matches(float(txt), dec, leaves):
            unverified.append(txt)
    return {"unknown_evidence": unknown, "unverified_numbers": unverified, "grounded": not unknown and not unverified}


# ------------------------------------------------------------------ LLM path

SYSTEM = """You are {agent}, part of OrbitalGuard, an advisory collision-risk assistant for a human \
satellite operator. {role}

Rules:
- Answer only from the CONTEXT JSON below. Every number you state must appear in it; never estimate \
positions, distances, delta-v or probabilities yourself. If the context does not contain the answer, say so.
- Cite the evidence IDs (E1, E2, ...) you used{director_note}.
- This is screening from SGP4 point estimates without covariance: do not give a collision probability \
and do not call a maneuver "safe".
- You are advisory only. Nothing is executed; the operator approves or rejects.
- Be concise: 1-4 sentences. Reply in the same language as the operator's question.
- Reply with a JSON object only: {{"answer": "<text>", "evidence": ["E1", ...]}}

CONTEXT:
{context}"""

ROLE_EVENT = "You answer questions about one close-approach event and its investigation."
ROLE_DIRECTOR = ("You coordinate all active alerts: help the operator decide which alert to review first and "
                 "summarise their status. Refer to alerts by their event id.")


def _llm_answer(client, agent: str, event: dict | None, ctx: dict, history: list[dict], question: str) -> dict:
    system = SYSTEM.format(agent=agent, role=ROLE_EVENT if event else ROLE_DIRECTOR,
                           director_note="" if event else " (there are none in the director context; use an empty list)",
                           context=json.dumps(ctx, ensure_ascii=False))
    messages = [{"role": "system", "content": system}]
    for turn in history[-MAX_HISTORY_TURNS:]:
        content = turn["content"]
        if turn["role"] == "assistant":  # keep earlier answers in the reply format so the model keeps using it
            content = json.dumps({"answer": content, "evidence": []}, ensure_ascii=False)
        messages.append({"role": turn["role"], "content": content})
    messages.append({"role": "user", "content": question})
    resp = client.chat.completions.create(model=config.MODEL, messages=messages, max_tokens=1500,
                                          response_format={"type": "json_object"})
    text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    data = _parse_json(text)
    if data is None and text:  # model answered in prose: accept it, pick up any cited evidence IDs
        data = {"answer": text, "evidence": sorted(set(re.findall(r"\bE\d+\b", text)), key=lambda e: int(e[1:]))}
    if not data or not isinstance(data.get("answer"), str) or not data["answer"].strip():
        raise ValueError("model reply was empty or not the expected JSON")
    cited = [str(e) for e in data.get("evidence") or [] if isinstance(e, (str, int))]
    return {"answer": data["answer"].strip(), "evidence": cited}


# ------------------------------------------------------------------ deterministic fallback

def _topic(q: str) -> str:
    t = q.lower()
    if re.search(r"new (close |near )?(approach|conjunction)|secondary|conflict|二次|新的(交会|接近)|冲突", t):
        return "conflicts"
    if re.search(r"first|priorit|which alert|order|先|优先|哪个告警", t):
        return "priority"
    if re.search(r"maneuver|manoeuvre|burn|delta.?v|option|compare|机动|变轨|方案|点火", t):
        return "maneuver"
    if re.search(r"risk|severity|threshold|red|amber|danger|风险|等级|阈值|危险", t):
        return "risk"
    return "summary"


def _fallback_answer(event: dict | None, ctx: dict, evidence: dict, question: str) -> dict:
    topic = _topic(question)
    if event is None:
        alerts = ctx.get("alerts", [])
        if not alerts:
            return {"answer": "No RED or AMBER alert is active.", "evidence": []}
        order = sorted(alerts, key=lambda a: (a["severity"] != "RED", a["status"] != "awaiting_approval", a["miss_km"]))
        top = order[0]
        rest = "; ".join(f"{a['id']} {a['severity']} {a['miss_km']} km ({a['status'] or 'not investigated'})" for a in order[1:])
        return {"answer": f"Review {top['id']} first: {top['severity']}, {top['miss_km']} km predicted miss, status "
                          f"{top['status'] or 'not investigated'}." + (f" Then: {rest}." if rest else ""), "evidence": []}
    ev = ctx["event"]
    th = ctx["thresholds"]
    inv = ctx.get("investigation") or {}
    rec = inv.get("recommendation") or {}
    comp = next((e for e in reversed(list(evidence.items())) if e[1]["tool"] == "compare_maneuvers"), None)
    ca = next((e for e in evidence.items() if e[1]["tool"] == "analyze_close_approach"), None)
    if topic == "risk" or topic == "priority":
        level = "RED" if ev["miss_km"] < th["red_km"] else "AMBER" if ev["miss_km"] < th["amber_km"] else "WATCH"
        return {"answer": f"Predicted miss is {ev['miss_km']} km at {ev['tca_utc']}, so the event is {level} "
                          f"(RED below {th['red_km']} km, AMBER below {th['amber_km']} km).", "evidence": [ca[0]] if ca else []}
    if topic in ("maneuver", "conflicts"):
        if not comp:
            return {"answer": "No maneuver comparison is available for this event yet.", "evidence": []}
        out = comp[1]["output"] or {}
        best = out.get("best_option")
        row = next((r for r in out.get("options", []) if r.get("option_id") == best or r.get("maneuver_id") == best), None)
        if topic == "conflicts":
            if row is None:
                return {"answer": "No simulated option met the target, so there is no preferred option to re-screen.", "evidence": [comp[0]]}
            n = row.get("new_conjunctions", 0)
            return {"answer": f"Re-screening found {n} new close approach(es) for option {best}.", "evidence": [comp[0]]}
        if row is None:
            return {"answer": "No simulated option met the target miss distance without new conjunctions.", "evidence": [comp[0]]}
        miss = row.get("predicted_miss_km", row.get("new_miss_km"))
        return {"answer": f"Option {best} is preferred: {row.get('delta_v_m_s')} m/s, predicted miss {miss} km.", "evidence": [comp[0]]}
    if rec:
        return {"answer": f"{rec.get('headline')}. Recommended action: {rec.get('recommended_action')}.", "evidence": []}
    return {"answer": "The investigation for this event has not finished yet.", "evidence": []}


# ------------------------------------------------------------------ entry point

def answer(mission, event: dict | None, question: str, history: list[dict], client=None) -> dict:
    global _llm_down_until
    agent = agent_name(event["id"] if event else None)
    ctx, evidence = build_context(mission, event)
    result: dict[str, Any] = {"agent": agent, "event_id": event["id"] if event else None}
    reason = None
    use_llm = config.llm_configured() or client is not None
    if use_llm and client is None and time.monotonic() < _llm_down_until:
        use_llm, reason = False, "LLM recently unreachable; using deterministic answers"
    if use_llm:
        try:
            client = client or make_client(timeout=config.CHAT_TIMEOUT_S, max_retries=0)
            out = _llm_answer(client, agent, event, ctx, history, question)
            result.update(out, mode="llm", model=config.MODEL)
        except openai.APIConnectionError:
            with _down_lock:
                _llm_down_until = time.monotonic() + config.CHAT_LLM_BACKOFF_S
            reason = "LLM unreachable (connection error/timeout)"
        except openai.APIStatusError as e:
            reason = f"LLM API error {e.status_code}"
        except (ValueError, KeyError, TypeError) as e:
            reason = f"LLM reply unusable: {e}"
    elif reason is None:
        reason = "LLM not configured"
    if "answer" not in result:
        result.update(_fallback_answer(event, ctx, evidence, question), mode="fallback", model="deterministic", reason=reason)
    result["check"] = check_answer(result["answer"], result["evidence"], ctx, evidence)
    db.audit("operator", "chat", {"agent": agent, "event": result["event_id"], "question": question[:500],
                                  "mode": result["mode"], "grounded": result["check"]["grounded"]})
    return result
