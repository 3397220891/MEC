"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ChatTurn, CloseEvent, ComparisonRow, Investigation, MissionState } from "@/lib/types";

const questions = [
  { id: "risk", label: "Why this risk level?" },
  { id: "maneuver", label: "Which maneuver looks best?" },
  { id: "conflicts", label: "Any new close approaches?" },
] as const;

type Topic = (typeof questions)[number]["id"];
type Exchange = {
  key: number;
  eventId: string;
  agentId: string | null; // null = Mission Director
  prompt: string;
  answer: string;
  topic?: Topic;
  comparison?: [string, string];
  evidence?: string;
  pending?: boolean;
  mode?: "llm" | "fallback" | "offline";
  model?: string;
  evidenceIds?: string[];
  warn?: string;
};

const DIRECTOR_QUESTION = "Which alert should I review first?";

function agentName(eventId: string) {
  return `Sentinel ${eventId.replace(/^EVT-/, "")}`;
}

function questionTopic(prompt: string): Topic | undefined {
  const text = prompt.toLowerCase();
  if (/new (close |near )?(approach|conjunction)|secondary (risk|conjunction)|二次|新的交会/.test(text)) return "conflicts";
  if (/risk|severity|threshold|alert level|red|amber|风险|等级|阈值/.test(text)) return "risk";
  if (/maneuver|manoeuvre|delta.?v|compare|option|机动|方案/.test(text)) return "maneuver";
}

export default function EventQuestions({ event, events, investigations, onSelectEvent, investigation, thresholds, options, optionId, onEvidence }: {
  event: CloseEvent;
  events: CloseEvent[];
  investigations: MissionState["investigations"];
  onSelectEvent: (id: string) => void;
  investigation: Investigation | null;
  thresholds?: MissionState["thresholds_km"];
  options: ComparisonRow[];
  optionId: string;
  onEvidence: (id: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [recipient, setRecipient] = useState<"event" | "director">("event");
  const messages = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (messages.current) messages.current.scrollTop = messages.current.scrollHeight;
  }, [exchanges]);
  const alerts = events.filter((item) => item.severity === "RED" || item.severity === "AMBER");
  const priority = alerts.filter((alert) =>
    investigations.find((item) => item.event_id === alert.id)?.status === "awaiting_approval"
  ).sort((a, b) => Number(b.severity === "RED") - Number(a.severity === "RED") || a.tca_s - b.tca_s)[0];
  const candidate = options.find((row) => row.option_id === optionId && row.option_id !== "NO_MANEUVER");
  const baseline = options.find((row) => row.option_id === "NO_MANEUVER");
  const action = investigation?.recommendation?.headline;
  const limit = thresholds && {
    RED: `below the ${thresholds.red} km RED threshold`,
    AMBER: `at or above ${thresholds.red} km and below the ${thresholds.amber} km AMBER threshold`,
    WATCH: `at or above the ${thresholds.amber} km AMBER threshold and within the ${thresholds.screen} km screening range`,
  }[event.severity];

  const unavailable = options.length
    ? "The comparison did not identify a preferred maneuver."
    : "A maneuver comparison is unavailable for this event.";
  const answers = {
    risk: `The predicted miss distance is ${event.miss_km.toFixed(3)} km${limit ? `, ${limit}` : ""}. The screening level is ${event.severity}.`,
    maneuver: candidate
      ? `${action ? `${action}. ` : ""}Option ${candidate.option_id} uses ${candidate.delta_v_m_s} m/s Δv. Predicted separation is ${candidate.predicted_miss_km.toFixed(3)} km${baseline ? ` versus ${baseline.predicted_miss_km.toFixed(3)} km without a maneuver` : ""}.`
      : [action, unavailable].filter(Boolean).join(". "),
    conflicts: candidate
      ? candidate.new_conjunctions === 0
        ? `The re-screening found no new close approaches for option ${candidate.option_id}.`
        : `The re-screening flagged ${candidate.new_conjunctions} new close approach(es) for option ${candidate.option_id}.`
      : unavailable,
  };

  async function send(prompt: string, topic = questionTopic(prompt)) {
    const message = prompt.trim();
    if (!message) return;
    const toDirector = recipient === "director";
    const agentId = toDirector ? null : event.id;
    const evidence = toDirector ? undefined : topic === "risk"
      ? "event-screening-details"
      : topic && candidate && options.length > 1 ? "event-maneuver-comparison" : undefined;
    const comparison: Exchange["comparison"] = !toDirector && topic === "maneuver" && candidate && baseline
      ? [baseline.predicted_miss_km.toFixed(3), candidate.predicted_miss_km.toFixed(3)]
      : undefined;
    // Used only if the backend cannot be reached at all.
    const local = toDirector
      ? priority ? `Review ${agentName(priority.id)} first. This ${priority.severity} alert is awaiting operator approval.` : "No alert is awaiting approval."
      : topic ? answers[topic] : "I can explain this event’s risk level, compare maneuvers, or check for new close approaches.";
    // Recent turns with the same agent; the server adds the mission context on every call.
    const history: ChatTurn[] = exchanges
      .filter((x) => x.agentId === agentId && !x.pending && x.answer)
      .slice(-4)
      .flatMap((x) => [{ role: "user" as const, content: x.prompt }, { role: "assistant" as const, content: x.answer }]);
    const key = Date.now() + Math.random();
    setExchanges((previous) => [...previous, { key, eventId: event.id, agentId, prompt: message, topic, evidence, comparison, answer: "", pending: true }]);
    setDraft("");
    let patch: Partial<Exchange>;
    try {
      const r = await api.chat(message, agentId, history);
      const issues = [...r.check.unknown_evidence.map((e) => `unknown ${e}`), ...r.check.unverified_numbers.map((n) => `unverified ${n}`)];
      patch = { answer: r.answer, mode: r.mode, model: r.model, evidenceIds: r.evidence, warn: issues.length ? issues.join(", ") : undefined };
    } catch {
      patch = { answer: local, mode: "offline" };
    }
    setExchanges((previous) => previous.map((x) => (x.key === key ? { ...x, ...patch, pending: false } : x)));
  }

  return (
    <section aria-label="Collision chat" className="mt-3 shrink-0 overflow-hidden rounded-xl border border-line bg-bg/80">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2.5 text-xs">
        <span className={`h-1.5 w-1.5 rounded-full ${event.severity === "RED" ? "bg-red" : "bg-amber"}`} aria-hidden="true" />
        <span className="font-semibold text-ink">{event.severity}</span>
        <span className="min-w-0 truncate text-muted" title={`${event.primary_name} × ${event.secondary_name}`}>
          {event.primary_name} × {event.secondary_name}
        </span>
      </div>
      <div ref={messages} role="log" aria-label="Chat messages" tabIndex={0}
        className="scroll-thin max-h-60 min-h-32 space-y-3 overflow-y-auto px-3 py-4 text-xs leading-relaxed">
        {alerts.map((alert) => (
          <div key={alert.id} className="flex items-start gap-2">
            <span aria-hidden="true" className={`grid size-6 shrink-0 place-items-center rounded-md border border-line bg-panel-2 ${alert.severity === "RED" ? "text-red" : "text-amber"}`}>◎</span>
            <div className="min-w-0 text-ink-2">
              <button type="button" aria-pressed={alert.id === event.id} onClick={() => onSelectEvent(alert.id)}
                className="text-left font-semibold text-ink hover:text-accent aria-pressed:text-accent">
                {agentName(alert.id)}
              </button>
              <p>{alert.primary_name} × {alert.secondary_name}</p>
              <p className="mt-1">
                <strong className="text-ink">{alert.miss_km.toFixed(3)} km</strong> predicted miss · {alert.tca_utc.slice(11, 19)} UTC · {alert.severity}
              </p>
            </div>
          </div>
        ))}
        <div className="rounded-lg border border-accent/35 bg-accent/5 px-3 py-2.5 text-ink-2">
          <p className="font-semibold text-accent">Mission Director</p>
          {priority ? (
            <>
              <p>Review <strong className="text-ink">{agentName(priority.id)}</strong> first. This {priority.severity} alert is awaiting operator approval.</p>
              <button type="button" onClick={() => onSelectEvent(priority.id)}
                className="mt-1.5 text-watch underline underline-offset-2">
                Select priority alert ↗
              </button>
            </>
          ) : <p>No alert is awaiting approval. Continue monitoring active investigations.</p>}
          <p className="mt-1 text-muted">Final approval belongs to the operator.</p>
        </div>
        {exchanges.map((entry) => (
          <div key={entry.key} className="space-y-2.5">
            <p className="ml-auto w-fit max-w-[90%] break-words rounded-xl rounded-br-sm border border-line bg-panel-2 px-2.5 py-1.5 text-ink">
              <span className="sr-only">You: </span>{entry.prompt}
            </p>
            <div className="flex items-start gap-2 text-ink-2">
              <span aria-hidden="true" className="grid size-6 shrink-0 place-items-center rounded-md border border-line bg-panel-2 text-accent">◎</span>
              <div className="min-w-0">
                <p className="font-semibold text-ink">
                  {entry.agentId ? agentName(entry.agentId) : "Mission Director"}
                  {entry.mode && (
                    <span className="ml-1.5 rounded border border-line px-1 py-px text-[10px] font-normal text-muted">
                      {entry.mode === "llm" ? entry.model : entry.mode === "fallback" ? "rule-based" : "local"}
                    </span>
                  )}
                </p>
                {entry.pending ? <p className="animate-pulse text-watch">● thinking…</p> : <p className="whitespace-pre-wrap">{entry.answer}</p>}
                {!!entry.evidenceIds?.length && (
                  <p className="mt-1 font-mono text-[10px] text-muted">evidence: {entry.evidenceIds.join(" · ")}</p>
                )}
                {entry.warn && <p className="mt-1 text-[10px] text-amber">⚠ check: {entry.warn}</p>}
                {entry.comparison && (
                  <p className="mt-2 rounded-md border border-line bg-panel-2/80 px-2 py-1.5 font-mono tabular-nums text-accent">
                    {entry.comparison[0]} → {entry.comparison[1]} km
                  </p>
                )}
                {entry.evidence && (
                  <button type="button" onClick={() => {
                    if (entry.eventId !== event.id) onSelectEvent(entry.eventId);
                    else if (entry.evidence) onEvidence(entry.evidence);
                  }} className="mt-1.5 text-watch underline underline-offset-2">
                    {entry.eventId !== event.id ? "Switch to this event" : entry.topic === "risk" ? "View event data" : "View maneuver evidence"} ↗
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="border-t border-line bg-panel-2/35 p-2.5">
        <div className="mb-2 flex items-center gap-1.5 text-[11px] text-muted" role="radiogroup" aria-label="Ask">
          <span>Ask</span>
          {(["event", "director"] as const).map((r) => (
            <button key={r} type="button" role="radio" aria-checked={recipient === r} onClick={() => setRecipient(r)}
              className="rounded-md border border-line px-1.5 py-0.5 text-ink-2 aria-checked:border-accent/60 aria-checked:text-accent">
              {r === "event" ? agentName(event.id) : "Mission Director"}
            </button>
          ))}
        </div>
        <div className="mb-2 flex flex-wrap gap-1.5">
          {(recipient === "director" ? [{ id: undefined, label: DIRECTOR_QUESTION }] : questions).map(({ id, label }) => (
            <button key={label} type="button" onClick={() => send(label, id)}
              className="rounded-full border border-line px-2 py-0.5 text-left text-[11px] text-ink-2 hover:border-accent/60 hover:text-accent">
              {label}
            </button>
          ))}
        </div>
        <form className="flex min-w-0 items-center gap-1 rounded-lg border border-line bg-bg px-1.5 py-1 focus-within:border-accent/60"
          onSubmit={(event) => { event.preventDefault(); send(draft); }}>
          <input aria-label="Ask about this event" value={draft} onChange={(event) => setDraft(event.target.value)}
            maxLength={400} placeholder={recipient === "director" ? "Ask the Mission Director…" : "Ask about this event…"} className="min-w-0 flex-1 bg-transparent px-1 py-1 text-xs text-ink placeholder:text-muted focus:outline-none" />
          <button type="submit" disabled={!draft.trim()} className="rounded-md bg-accent px-2 py-1 text-xs font-semibold text-black disabled:opacity-40">
            Send
          </button>
        </form>
      </div>
    </section>
  );
}
