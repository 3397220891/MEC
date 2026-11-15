"""Investigation orchestrator.

LLM mode       The model plans: it decides which tools to call, in what order, with which
               maneuver parameters, and when it has enough evidence. Loop budget: MAX_TURNS
               model turns and MAX_WALL_S seconds.
Rule-based     If no LLM provider is reachable, a fixed deterministic planner drives the same
               tools so the demo and the safety workflow keep working. It is labelled as such
               everywhere; it is a fallback, not the product.

Both modes end in status "awaiting_approval". Nothing is executed without a human decision.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Optional

from ..llm import LLM, LLMError
from ..orbital.timeutil import iso
from ..world import World
from . import tools as T

MAX_TURNS = 24
MAX_WALL_S = 240
MAX_NUDGES = 2

SYSTEM_PROMPT = """You are OrbitalGuard, an autonomous collision-investigation agent supporting a satellite \
operations team. A screening system has flagged a close approach. Investigate it with the tools and hand a \
recommendation to the human flight director.

How you work
- All numbers must come from tool outputs. Never estimate positions, distances, probabilities or delta-v yourself.
- Collision probability is NOT available (no covariance). Do not claim a Pc or that something is "safe"; talk about \
miss distance relative to the indicative uncertainty.
- Investigate before concluding: get info on both objects (data source, element age), analyze the close approach, \
then explore maneuvers.
- Explore the maneuver space deliberately: a no-maneuver baseline (dv 0), prograde AND retrograde along-track burns, \
at least two burn timings (e.g. ~1 orbit and ~1/2 orbit before TCA). If no option meets the criteria, adapt \
(larger dv or different timing) and simulate again. Watch secondary_conjunctions: a burn that fixes one event can \
create another.
- Use compare_maneuvers on all candidates (min_safe_miss_km 1.0 unless evidence suggests otherwise), then \
generate_incident_report, then submit_recommendation citing evidence ids (E#).
- Prefer the lowest delta-v acceptable option. If nothing is acceptable, decide 'escalate'.
- Before each tool call, write one short sentence explaining why you are calling it (shown to operators).
- You are decision SUPPORT. Nothing you do commands a spacecraft."""


def _briefing(world: World, event: dict) -> str:
    a = world.catalog.get(event["primary_id"])
    b = world.catalog.get(event["secondary_id"])
    prov = world.provenance()
    return (
        f"AUTOMATED SCREENING ALERT {event['id']} (level {event['level']}).\n"
        f"Primary: {a.id} \"{a.name}\" (maneuverable={a.maneuverable}). Secondary: {b.id} \"{b.name}\" ({b.kind}).\n"
        f"Screened TCA {event['tca']}, miss {event['miss_km']} km, relative speed {event['rel_speed_km_s']} km/s.\n"
        f"Current simulation time: {iso(world.sim_now())}. Screening horizon ends "
        f"{world.horizon_s / 3600:.1f} h after scenario start.\n"
        f"Data: {prov['data_source']} ({prov['data_source_detail']}); simulated={prov['simulated']}.\n"
        "Investigate and submit a recommendation for human approval."
    )


class Investigations:
    def __init__(self, world: World, llm_factory):
        self.world = world
        self.llm_factory = llm_factory
        self._threads: dict[str, threading.Thread] = {}
        self._cancel: set[str] = set()

    def start(self, event_id: str, force_rule_based: bool = False, use_cache: bool = True) -> str:
        w = self.world
        event = w.event(event_id)
        for inv in w.db.investigations(w.scenario_id):
            if inv["event_id"] == event_id and inv["status"] == "running":
                return inv["id"]
        raw = f"{w.scenario_id}|{event_id}|{time.time()}"
        inv_id = "INV-" + hashlib.sha1(raw.encode()).hexdigest()[:8].upper()
        llm: Optional[LLM] = None if force_rule_based else self._llm()
        mode = "llm" if llm else "rule-based"
        w.db.create_investigation(inv_id, w.scenario_id, event, mode)
        w.db.log(w.scenario_id, "info", "agent", f"Investigation {inv_id} started for {event_id} ({mode} mode)",
                 {"investigation_id": inv_id, "event_id": event_id})
        ctx = T.Context(world=w, inv_id=inv_id, event=event)
        th = threading.Thread(target=self._run, args=(ctx, llm, use_cache), daemon=True, name=inv_id)
        self._threads[inv_id] = th
        th.start()
        return inv_id

    def cancel(self, inv_id: str) -> None:
        self._cancel.add(inv_id)

    def wait(self, inv_id: str, timeout: float = 300) -> None:
        if inv_id in self._threads:
            self._threads[inv_id].join(timeout)

    def _llm(self) -> Optional[LLM]:
        try:
            llm = self.llm_factory()
            return llm if llm.providers else None
        except Exception:  # noqa: BLE001 - any config problem means "no LLM"
            return None

    # ------------------------------------------------------------------
    def _run(self, ctx: T.Context, llm: Optional[LLM], use_cache: bool) -> None:
        db = ctx.db
        try:
            if llm is not None:
                try:
                    self._run_llm(ctx, llm, use_cache)
                except LLMError as e:
                    db.add_step(ctx.inv_id, "system", "llm_unavailable", None,
                                {"error": str(e), "action": "switching to rule-based planner"}, "error")
                    db.update_investigation(ctx.inv_id, mode="rule-based (LLM failed mid-run)")
                    db.log(self.world.scenario_id, "warn", "agent", f"{ctx.inv_id}: LLM unavailable, rule-based fallback")
            if ctx.recommendation is None and ctx.inv_id not in self._cancel:
                run_rule_based(ctx)
            if ctx.recommendation is None:
                raise RuntimeError("investigation ended without a recommendation")
            report = dict(ctx.report or {})
            report["recommendation"] = ctx.recommendation
            db.update_investigation(ctx.inv_id, status="awaiting_approval", report=report,
                                    recommendation=ctx.recommendation)
            db.log(self.world.scenario_id, "alert", "agent",
                   f"{ctx.inv_id}: recommendation ready ({ctx.recommendation['decision']}) - awaiting human approval",
                   {"investigation_id": ctx.inv_id})
        except Exception as e:  # noqa: BLE001 - surface every failure to the UI
            db.update_investigation(ctx.inv_id, status="failed", error=f"{type(e).__name__}: {e}")
            db.log(self.world.scenario_id, "error", "agent", f"{ctx.inv_id} failed: {e}")

    def _run_llm(self, ctx: T.Context, llm: LLM, use_cache: bool) -> None:
        db = ctx.db
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _briefing(self.world, ctx.event)}]
        started, nudges = time.time(), 0
        for _turn in range(MAX_TURNS):
            if ctx.inv_id in self._cancel:
                db.add_step(ctx.inv_id, "system", "cancelled", None, None, "error")
                return
            if time.time() - started > MAX_WALL_S:
                db.add_step(ctx.inv_id, "system", "budget_exceeded", None, {"max_wall_s": MAX_WALL_S}, "error")
                return
            turn = llm.chat_tools(messages, T.TOOL_SCHEMAS, use_cache=use_cache)
            db.update_investigation(ctx.inv_id, provider=f"{turn.provider}:{turn.model}")
            db.add_step(ctx.inv_id, "llm", "reasoning", None,
                        {"text": turn.text, "tool_calls": [c.name for c in turn.tool_calls],
                         "provider": turn.provider, "model": turn.model, "cached": turn.cached,
                         "usage": turn.usage}, "ok", turn.latency_ms)
            messages.append(turn.assistant_message())
            if not turn.tool_calls:
                if ctx.recommendation is not None or nudges >= MAX_NUDGES:
                    return
                nudges += 1
                messages.append({"role": "user", "content": "Continue the investigation using the tools. "
                                 "You must finish by calling submit_recommendation."})
                continue
            for call in turn.tool_calls:
                _ref, payload = T.execute(ctx, call.name, call.arguments)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(payload, ensure_ascii=False, default=str)})
            if ctx.recommendation is not None:
                return
        db.add_step(ctx.inv_id, "system", "turn_budget_exceeded", None, {"max_turns": MAX_TURNS}, "error")


# ---------------------------------------------------------------------------
# Deterministic fallback planner
# ---------------------------------------------------------------------------

def run_rule_based(ctx: T.Context) -> None:
    w, ev = ctx.world, ctx.event
    db = ctx.db
    db.add_step(ctx.inv_id, "system", "planner", None,
                {"text": "Rule-based planner active (no LLM). Executing the standard investigation checklist."})
    primary = ev["primary_id"] if w.catalog.get(ev["primary_id"]).maneuverable else ev["secondary_id"]
    T.execute(ctx, "get_satellite_info", {"satellite_id": ev["primary_id"]})
    T.execute(ctx, "get_satellite_info", {"satellite_id": ev["secondary_id"]})
    T.execute(ctx, "get_trajectory", {"satellite_id": primary, "duration_min": min(360, w.horizon_s / 60)})
    ref_ca, _ = T.execute(ctx, "analyze_close_approach", {"satellite_a": ev["primary_id"], "satellite_b": ev["secondary_id"]})

    period_min = w.catalog.get(primary).mean_elements()["period_min"]
    tca_min = (ev["tca_offset_s"] - w.now_offset_s) / 60
    leads = [lead for lead in (round(period_min, 1), round(period_min / 2, 1)) if lead < tca_min - 11]
    ids: list[str] = []

    def sim(lead, dv, label):
        ref, payload = T.execute(ctx, "simulate_maneuver", {"satellite_id": primary, "event_id": ev["id"],
                                                            "burn_lead_time_min": lead, "dv_along_track_m_s": dv,
                                                            "label": label})
        if payload["ok"]:
            ids.append(payload["result"]["maneuver_id"])

    if leads:
        sim(leads[0], 0.0, "No maneuver (baseline)")
    for dv in (0.05, 0.1, 0.2, 0.4):
        for lead in leads:
            sim(lead, dv, f"Prograde {dv * 100:.0f} cm/s @ T-{lead:.0f} min")
            sim(lead, -dv, f"Retrograde {dv * 100:.0f} cm/s @ T-{lead:.0f} min")
        ref_cmp, cmp_payload = T.execute(ctx, "compare_maneuvers", {"maneuver_ids": ids})
        if cmp_payload["ok"] and cmp_payload["result"]["best_option"]:
            break
    T.execute(ctx, "generate_incident_report", {"event_id": ev["id"]})
    comp = ctx.last_comparison or {}
    best = comp.get("best_option")
    rows = {r["maneuver_id"]: r for r in comp.get("options", [])}
    if best:
        b = rows[best]
        rejected = [r for r in comp["options"] if r["rejection_reasons"]]
        args = {
            "decision": "maneuver", "recommended_maneuver_id": best,
            "summary": f"Rule-based checklist result: '{b['label']}' raises the predicted miss distance to "
                       f"{b['new_miss_km']} km for {b['dv_magnitude_m_s']} m/s and creates no new RED/AMBER "
                       f"conjunction in the re-screen. Awaiting flight director review.",
            "rationale": [f"{b['label']}: lowest delta-v option meeting the criteria ({ref_cmp})."] +
                         [f"Rejected {r['label']}: {'; '.join(r['rejection_reasons'])}" for r in rejected[:4]],
            "evidence_refs": [ref_ca, ref_cmp],
            "risks_and_limitations": T.LIMITATIONS[:3],
            "next_steps": ["Request higher-accuracy ephemeris / CDM for both objects.",
                           "Re-run screening with updated elements before the burn decision deadline.",
                           "Flight director approves or rejects the hypothetical plan."],
            "confidence": "medium",
        }
    else:
        args = {
            "decision": "escalate", "recommended_maneuver_id": None,
            "summary": "Rule-based checklist found no simulated option meeting the acceptance criteria. "
                       "Escalating to the flight dynamics team.",
            "rationale": ["No option in the comparison met the criteria."],
            "evidence_refs": [ref_ca],
            "risks_and_limitations": T.LIMITATIONS[:3],
            "next_steps": ["Flight dynamics team to design a custom maneuver.", "Request CDM from the 18th SDS."],
            "confidence": "low",
        }
    _, payload = T.execute(ctx, "submit_recommendation", args)
    if not payload["ok"]:  # grounding guardrail is strict; retries accept with a warning
        for _ in range(2):
            _, payload = T.execute(ctx, "submit_recommendation", args)
            if payload["ok"]:
                break
