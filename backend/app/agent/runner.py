"""Investigation orchestration: LLM tool-use loop (any OpenAI-compatible API) with guards, plus fallback."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

import openai

from .. import config, db
from ..services.state import STORE
from .offline import run_offline
from .report import RECOMMENDATION_SCHEMA, validate_recommendation
from .tools import TOOL_SCHEMAS, InvestigationContext, run_tool

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")
_contexts: dict[str, InvestigationContext] = {}
_lock = threading.Lock()

SYSTEM_PROMPT = """You are OrbitalGuard, a collision-investigation agent that supports a human flight-dynamics operator. \
A monitoring system has flagged a predicted close approach between two tracked space objects. Your job is to \
investigate it with the analysis tools provided and hand the operator an evidence-based recommendation.

How to investigate:
- Establish the facts with tools before concluding anything: who the objects are, which (if any) can maneuver, how \
fresh the orbital data is, and the refined close-approach geometry and uncertainty.
- Judge the risk relative to the uncertainty. Screening thresholds used by this operator: RED below 1 km, AMBER \
below 2.5 km, WATCH below 5 km. A sensible avoidance target is at least the larger of 2 km and twice the combined \
1-sigma position uncertainty.
- If the risk warrants it and an object can maneuver, simulate a small, diverse set of hypothetical maneuvers \
(vary direction, burn timing and delta-v; earlier burns usually need less delta-v). Check each result for new \
secondary conjunctions, then compare them. If nothing meets the target, explore further or say so.
- If the miss is ambiguous relative to the uncertainty, it can be right to recommend monitoring and requesting \
updated tracking, keeping a simulated maneuver as a contingency. Do not recommend a burn the evidence does not support.
- Independent tool calls can be issued in parallel. Budget: about 14 tool calls in total.
- Call generate_incident_report once your analysis is complete, then give your final answer.

Ground rules:
- Every number you state must come from a tool result. Never estimate orbital positions, distances, delta-v or \
probabilities yourself. Cite the evidence IDs (E1, E2, ...) returned by the tools for every claim.
- This is screening based on SGP4 point estimates without covariance: do not state a collision probability, and do \
not call a maneuver "safe"; describe predicted miss distances under the stated model assumptions.
- You are advisory only. Nothing is executed; a human operator must approve any hypothetical action.

Final answer: a JSON object matching the required schema. Keep each rationale claim to one or two sentences. Use \
recommended_maneuver_id = "" unless the action is PREPARE_MANEUVER_FOR_APPROVAL or you name a contingency option.
Reply with the JSON object only (no prose, no code fences). Required JSON schema:
""" + json.dumps(RECOMMENDATION_SCHEMA)

# OpenAI-style function definitions built from the tool schemas.
OPENAI_TOOLS = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["input_schema"]}} for t in TOOL_SCHEMAS]


def get_context(inv_id: str) -> InvestigationContext | None:
    return _contexts.get(inv_id)


def _update(inv_id: str, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = [db.dumps(v) if k in db.JSON_COLS else v for k, v in fields.items()]
    db.execute(f"UPDATE investigations SET {cols} WHERE id = ?", (*vals, inv_id))


def start_investigation(event_id: str, trigger: str = "auto") -> str:
    mission = STORE.require()
    event = next((e for e in mission.events if e["id"] == event_id), None)
    if event is None:
        raise KeyError(f"Unknown event '{event_id}' in active scenario")
    with _lock:
        existing = db.query_one(
            "SELECT id FROM investigations WHERE event_id = ? AND status IN ('queued','running','awaiting_approval') ORDER BY started_at DESC",
            (event_id,),
        )
        if existing and existing["id"] in _contexts and _contexts[existing["id"]].mission is mission:
            return existing["id"]
        n = (db.query_one("SELECT COUNT(*) AS n FROM investigations") or {"n": 0})["n"] + 1
        inv_id = f"INV-{n:04d}"
        mode = "llm" if config.llm_configured() else "fallback"
        db.execute(
            "INSERT INTO investigations (id, event_id, scenario_id, status, mode, model, started_at) VALUES (?,?,?,?,?,?,?)",
            (inv_id, event_id, mission.scenario_id, "queued", mode, config.MODEL if mode == "llm" else "deterministic-planner", db.now_iso()),
        )
        ctx = InvestigationContext(id=inv_id, mission=mission, event=event)
        _contexts[inv_id] = ctx
    db.audit("system", "investigation_started", {"investigation": inv_id, "event": event_id, "trigger": trigger, "mode": mode})
    threading.Thread(target=_run, args=(ctx, mode), daemon=True, name=inv_id).start()
    return inv_id


def _run(ctx: InvestigationContext, mode: str) -> None:
    _update(ctx.id, status="running")
    ctx.log("system", output={"message": f"Investigation triggered for {ctx.event['id']} ({ctx.event['severity']}, miss {ctx.event['miss_km']} km)."})
    try:
        if mode == "llm":
            try:
                rec = _run_llm(ctx)
            except _FallbackNeeded as e:
                mode = "llm+fallback"
                _update(ctx.id, mode=mode)
                rec = run_offline(ctx, str(e))
        else:
            rec = run_offline(ctx, "LLM not configured (no DEEPSEEK_API_KEY / LLM_API_KEY)")
        _finalize(ctx, rec, mode)
    except Exception as e:  # last-resort guard: never leave an investigation hanging
        log.exception("investigation failed")
        ctx.log("system", output={"message": f"Investigation failed: {type(e).__name__}: {e}"}, status="error")
        _update(ctx.id, status="failed", error=f"{type(e).__name__}: {e}", finished_at=db.now_iso())


def _finalize(ctx: InvestigationContext, rec: dict, mode: str) -> None:
    if ctx.report is None:
        run_tool(ctx, "generate_incident_report", {"event_id": ctx.event["id"]}, "system")
    validation = validate_recommendation(ctx, rec)
    ctx.log("final", output={"recommendation": rec, "validation": validation}, status="ok" if validation["valid"] else "error")
    _update(ctx.id, status="awaiting_approval", recommendation=rec, validation=validation, report=ctx.report,
            finished_at=db.now_iso(), mode=mode)


class _FallbackNeeded(Exception):
    pass


def _tool_exec(ctx, name, inp):
    fut = _pool.submit(run_tool, ctx, name, inp, "llm-agent")
    try:
        return fut.result(timeout=config.TOOL_TIMEOUT_S)
    except FutureTimeout:
        ctx.log("tool", name, inp, {"error": "tool timeout"}, "error")
        return {"error": f"tool timed out after {config.TOOL_TIMEOUT_S}s"}, None, True


def _log_reasoning(ctx, msg) -> None:
    # Log one thought per turn: the model's own narration (`content`) when it gives one alongside tool calls,
    # otherwise the raw reasoning (`reasoning_content`, returned by DeepSeek thinking models). Logging both
    # showed two near-identical entries per step in the timeline.
    narration = (msg.content or "").strip() if msg.tool_calls else ""
    thinking = getattr(msg, "reasoning_content", None) or (getattr(msg, "model_extra", None) or {}).get("reasoning_content")
    text = narration or (thinking or "").strip()
    if text:
        ctx.log("thought", output={"text": text})


def _parse_json(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            value = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def make_client(timeout: float = 90.0, max_retries: int = 2) -> openai.OpenAI:
    return openai.OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY, timeout=timeout, max_retries=max_retries)


def _run_llm(ctx: InvestigationContext, client: openai.OpenAI | None = None) -> dict:
    client = client or make_client()
    ev = ctx.event
    brief = {k: ev[k] for k in ("id", "primary_id", "primary_name", "secondary_id", "secondary_name", "tca_utc", "tca_s",
                                "miss_km", "rel_speed_km_s", "miss_ric_km", "severity", "data_label", "detected_by")}
    brief["scenario_epoch_utc"] = ctx.mission.epoch.iso()
    brief["horizon_min"] = ctx.mission.horizon_s / 60
    messages: list = [{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": "Close-approach event flagged by monitoring:\n" + json.dumps(brief, indent=2)
                       + "\n\nInvestigate it and give your recommendation."}]
    deadline = time.monotonic() + config.AGENT_WALL_CLOCK_S
    tool_calls, repaired, tools_closed = 0, False, False

    while True:
        if time.monotonic() > deadline:
            raise _FallbackNeeded(f"agent wall-clock limit {config.AGENT_WALL_CLOCK_S:.0f}s exceeded")
        try:
            response = client.chat.completions.create(
                model=config.MODEL,
                max_tokens=8000,
                messages=messages,
                tools=OPENAI_TOOLS,
                tool_choice="none" if tools_closed else "auto",
            )
        except openai.AuthenticationError as e:
            raise _FallbackNeeded(f"LLM authentication failed ({e.status_code})")
        except openai.RateLimitError:
            raise _FallbackNeeded("LLM rate limited")
        except openai.APIStatusError as e:
            raise _FallbackNeeded(f"LLM API error {e.status_code}: {str(e.message)[:200]}")
        except openai.APIConnectionError:
            raise _FallbackNeeded("LLM unreachable (connection error/timeout)")

        if not response.choices:
            raise _FallbackNeeded("LLM returned no choices")
        choice = response.choices[0]
        msg = choice.message
        _log_reasoning(ctx, msg)
        if choice.finish_reason == "length":
            raise _FallbackNeeded("LLM response truncated (max_tokens)")
        if choice.finish_reason == "content_filter":
            raise _FallbackNeeded("LLM response blocked by content filter")
        # Keep the full assistant turn (incl. reasoning_content / tool_calls) so the API sees a consistent history.
        messages.append(msg.model_dump(exclude_none=True))

        if msg.tool_calls:
            for call in msg.tool_calls:
                tool_calls += 1
                if tool_calls > config.AGENT_MAX_TOOL_CALLS:
                    payload, is_err = {"error": "tool budget exhausted; produce the final JSON answer now"}, True
                    tools_closed = True
                else:
                    try:
                        args = json.loads(call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = None
                    if not isinstance(args, dict):
                        payload, is_err = {"error": "tool arguments were not a valid JSON object"}, True
                    else:
                        payload, _, is_err = _tool_exec(ctx, call.function.name, args)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(payload)})
            continue

        text = (msg.content or "").strip()
        rec = _parse_json(text)
        if rec is not None:
            check = validate_recommendation(ctx, rec)
            if check["valid"] or repaired:
                return rec
            problems = check["errors"]
        else:
            if repaired:
                raise _FallbackNeeded("LLM final answer was not valid JSON")
            problems = ["final answer was not valid JSON"]
        repaired = True
        ctx.log("system", output={"message": "Recommendation failed validation; asking agent to correct it.", "problems": problems}, status="error")
        messages.append({"role": "user", "content": "Your recommendation failed server validation: " + "; ".join(problems)
                         + ". Correct it (cite only evidence IDs that exist, reference only simulated maneuver IDs) and answer again."})
