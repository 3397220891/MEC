"use client";

import { useEffect, useRef, useState } from "react";
import type { CloseEvent, ComparisonRow, Investigation, MissionState } from "@/lib/types";

const questions = [
  { id: "risk", label: "Why this risk level?" },
  { id: "maneuver", label: "Which maneuver looks best?" },
  { id: "conflicts", label: "Any new close approaches?" },
] as const;

type Topic = (typeof questions)[number]["id"];
type Exchange = { eventId: string; prompt: string; answer: string; topic?: Topic; comparison?: [string, string]; evidence?: string };

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

  function send(prompt: string, topic = questionTopic(prompt)) {
    const message = prompt.trim();
    if (!message) return;
    const evidence = topic === "risk"
      ? "event-screening-details"
      : topic && candidate && options.length > 1 ? "event-maneuver-comparison" : undefined;
    const comparison: Exchange["comparison"] = topic === "maneuver" && candidate && baseline
      ? [baseline.predicted_miss_km.toFixed(3), candidate.predicted_miss_km.toFixed(3)]
      : undefined;
    setExchanges((previous) => [...previous, {
      eventId: event.id, prompt: message, topic, evidence, comparison,
      answer: topic ? answers[topic] : "I can explain this event’s risk level, compare maneuvers, or check for new close approaches.",
    }]);
    setDraft("");
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
        {exchanges.map((entry, index) => (
          <div key={index} className="space-y-2.5">
            <p className="ml-auto w-fit max-w-[90%] break-words rounded-xl rounded-br-sm border border-line bg-panel-2 px-2.5 py-1.5 text-ink">
              <span className="sr-only">You: </span>{entry.prompt}
            </p>
            <div className="flex items-start gap-2 text-ink-2">
              <span aria-hidden="true" className="grid size-6 shrink-0 place-items-center rounded-md border border-line bg-panel-2 text-accent">◎</span>
              <div className="min-w-0">
                <p className="font-semibold text-ink">{agentName(entry.eventId)}</p>
                <p>{entry.answer}</p>
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
        <p className="mb-2 text-[11px] text-muted">Reply to <strong className="font-medium text-ink">{agentName(event.id)}</strong></p>
        <div className="mb-2 flex flex-wrap gap-1.5">
          {questions.map(({ id, label }) => (
            <button key={id} type="button" onClick={() => send(label, id)}
              className="rounded-full border border-line px-2 py-0.5 text-left text-[11px] text-ink-2 hover:border-accent/60 hover:text-accent">
              {label}
            </button>
          ))}
        </div>
        <form className="flex min-w-0 items-center gap-1 rounded-lg border border-line bg-bg px-1.5 py-1 focus-within:border-accent/60"
          onSubmit={(event) => { event.preventDefault(); send(draft); }}>
          <input aria-label="Ask about this event" value={draft} onChange={(event) => setDraft(event.target.value)}
            maxLength={400} placeholder="Ask about this event…" className="min-w-0 flex-1 bg-transparent px-1 py-1 text-xs text-ink placeholder:text-muted focus:outline-none" />
          <button type="submit" disabled={!draft.trim()} className="rounded-md bg-accent px-2 py-1 text-xs font-semibold text-black disabled:opacity-40">
            Send
          </button>
        </form>
      </div>
    </section>
  );
}
