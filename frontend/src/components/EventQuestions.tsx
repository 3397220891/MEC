"use client";

import { useEffect, useRef, useState } from "react";
import { chatStream } from "@/lib/api";
import type { ChatStreamEvent, ChatTurn, CloseEvent, ComparisonRow, Investigation, MissionState } from "@/lib/types";

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
  items: Item[]; // streamed transcript: reasoning, narration/answer text, tool calls with results
  toolCalls?: number;
};

type ToolResult = Extract<ChatStreamEvent, { type: "tool_result" }>;
type Item =
  | { kind: "thought"; text: string }
  | { kind: "text"; text: string }
  | { kind: "notice"; text: string }
  | { kind: "tool"; id: string; name: string; input: Record<string, unknown> | null; result?: ToolResult };

const DIRECTOR_QUESTION = "Which alert should I review first?";
const WHAT_IF = "What if we burn 90 min before TCA instead?";

/** Fold one stream event into the transcript (pure, so React state stays immutable). */
function applyEvent(x: Exchange, e: ChatStreamEvent): Exchange {
  const items = [...x.items];
  const last = items[items.length - 1];
  switch (e.type) {
    case "start":
      return { ...x, mode: e.mode, model: e.model };
    case "thought":
    case "text":
      if (last && last.kind === e.type) items[items.length - 1] = { ...last, text: last.text + e.delta };
      else items.push({ kind: e.type, text: e.delta });
      return { ...x, items };
    case "notice":
    case "error":
      items.push({ kind: "notice", text: e.message });
      return { ...x, items };
    case "tool_call":
      items.push({ kind: "tool", id: e.id, name: e.name, input: e.input });
      return { ...x, items };
    case "tool_result": {
      const i = items.findLastIndex((it) => it.kind === "tool" && (it.id === e.id || !it.result));
      if (i >= 0) items[i] = { ...(items[i] as Extract<Item, { kind: "tool" }>), result: e };
      return { ...x, items };
    }
    case "final": {
      const issues = [...e.check.unknown_evidence.map((id) => `unknown ${id}`), ...e.check.unverified_numbers.map((n) => `unverified ${n}`)];
      return { ...x, answer: e.answer, mode: e.mode, model: e.model, evidenceIds: e.evidence, toolCalls: e.tool_calls,
        warn: issues.length ? issues.join(", ") : undefined, pending: false };
    }
  }
}

function toolArgs(input: Record<string, unknown> | null): string {
  if (!input) return "invalid arguments";
  return Object.entries(input)
    .filter(([k]) => k !== "satellite_id" && k !== "secondary_id" && k !== "satellite_a" && k !== "satellite_b")
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`)
    .join(" · ");
}

function agentName(eventId: string) {
  return `Sentinel ${eventId.replace(/^EVT-/, "")}`;
}

function questionTopic(prompt: string): Topic | undefined {
  const text = prompt.toLowerCase();
  if (/new (close |near )?(approach|conjunction)|secondary (risk|conjunction)|二次|新的交会/.test(text)) return "conflicts";
  if (/risk|severity|threshold|alert level|red|amber|风险|等级|阈值/.test(text)) return "risk";
  if (/maneuver|manoeuvre|delta.?v|compare|option|机动|方案/.test(text)) return "maneuver";
}

export default function EventQuestions({ event, events, investigations, onSelectEvent, onReviewEvent, investigation, thresholds, options, optionId, onEvidence, onShowManeuver }: {
  event: CloseEvent;
  events: CloseEvent[];
  investigations: MissionState["investigations"];
  onSelectEvent: (id: string) => void;
  onReviewEvent: (id: string) => void;
  investigation: Investigation | null;
  thresholds?: MissionState["thresholds_km"];
  options: ComparisonRow[];
  optionId: string;
  onEvidence: (id: string) => void;
  onShowManeuver?: (investigationId: string, maneuverId: string) => void;
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
    setExchanges((previous) => [...previous, { key, eventId: event.id, agentId, prompt: message, topic, evidence, comparison, answer: "", pending: true, items: [] }]);
    setDraft("");
    const update = (fn: (x: Exchange) => Exchange) => setExchanges((previous) => previous.map((x) => (x.key === key ? fn(x) : x)));
    try {
      // Reasoning, every tool call the model makes, the computed results and the answer arrive as they happen.
      await chatStream(message, agentId, history, (e) => update((x) => applyEvent(x, e)));
      update((x) => (x.pending ? { ...x, pending: false, answer: x.answer || local, mode: x.mode ?? "offline" } : x));
    } catch {
      update((x) => ({ ...x, pending: false, answer: local, mode: "offline", items: [...x.items, { kind: "notice", text: "Backend unreachable; local answer" }, { kind: "text", text: local }] }));
    }
  }

  return (
    <section aria-label="Collision chat" className="mt-3 flex min-h-0 flex-col overflow-hidden rounded-xl border border-line bg-bg/80">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2.5 text-xs">
        <span className={`h-1.5 w-1.5 rounded-full ${event.severity === "RED" ? "bg-red" : "bg-amber"}`} aria-hidden="true" />
        <span className="font-semibold text-ink">{event.severity}</span>
        <span className="min-w-0 truncate text-muted" title={`${event.primary_name} × ${event.secondary_name}`}>
          {event.primary_name} × {event.secondary_name}
        </span>
      </div>
      <div className="shrink-0 border-b border-accent/35 bg-accent/5 px-3 py-2 text-xs text-ink-2">
        <p className="font-semibold text-accent">Mission Director</p>
        {priority ? (
          <>
            <p>Review <strong className="text-ink">{agentName(priority.id)}</strong> first. This {priority.severity} alert is awaiting operator approval.</p>
            <button type="button" onClick={() => onReviewEvent(priority.id)}
              className="mt-1.5 text-watch underline underline-offset-2">
              Review priority alert ↗
            </button>
          </>
        ) : <p>No alert is awaiting approval. Continue monitoring active investigations.</p>}
      </div>
      <div ref={messages} role="log" aria-label="Chat messages" tabIndex={0}
        className="scroll-thin min-h-32 flex-1 space-y-3 overflow-y-auto px-3 py-4 text-xs leading-relaxed">
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
                <div className="mt-1 space-y-1.5">
                  {entry.items.map((it, i) => {
                    if (it.kind === "thought") return (
                      <details key={i} className="rounded-md border border-line/60 bg-panel-2/40 px-2 py-1 text-[11px] text-muted">
                        <summary className="cursor-pointer select-none">💭 {entry.model ?? "model"} reasoning · {it.text.length} chars</summary>
                        <p className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap">{it.text}</p>
                      </details>
                    );
                    if (it.kind === "notice") return <p key={i} className="text-[11px] text-amber">! {it.text}</p>;
                    if (it.kind === "text") return <p key={i} className="whitespace-pre-wrap">{it.text}</p>;
                    return (
                      <div key={i} className="rounded-md border border-accent/30 bg-bg/60 px-2 py-1.5 font-mono text-[10.5px] leading-snug">
                        <p className="text-accent">🔧 {it.name}<span className="text-muted"> ({toolArgs(it.input)})</span></p>
                        {it.result ? (
                          <p className={it.result.ok ? "text-ink-2" : "text-amber"}>
                            {it.result.ok ? "✓" : "✗"} {it.result.evidence_id && <strong className="text-ink">{it.result.evidence_id} </strong>}{it.result.summary}
                            {it.result.maneuver_id && it.result.investigation_id && onShowManeuver && (
                              <button type="button" onClick={() => onShowManeuver(it.result!.investigation_id!, it.result!.maneuver_id!)}
                                className="ml-1.5 font-sans text-watch underline underline-offset-2">show on globe ↗</button>
                            )}
                          </p>
                        ) : <p className="animate-pulse text-watch">… running OrbitalGuard physics</p>}
                      </div>
                    );
                  })}
                  {entry.pending && <p className="animate-pulse text-watch">● {entry.items.length ? "working…" : "thinking…"}</p>}
                  {!entry.pending && !entry.items.some((it) => it.kind === "text") && <p className="whitespace-pre-wrap">{entry.answer}</p>}
                </div>
                {!entry.pending && entry.mode === "llm" && (
                  <p className="mt-1 text-[10px] text-muted">
                    {entry.toolCalls ? `${entry.toolCalls} tool call(s) computed by OrbitalGuard physics, requested by ${entry.model}` : `answered by ${entry.model} from existing evidence`}
                  </p>
                )}
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
      <div className="shrink-0 border-t border-line bg-panel-2/35 p-2.5">
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
          {(recipient === "director" ? [{ id: undefined, label: DIRECTOR_QUESTION }] : [...questions, { id: undefined, label: WHAT_IF }]).map(({ id, label }) => (
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
