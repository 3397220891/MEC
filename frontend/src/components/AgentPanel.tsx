/* eslint-disable @typescript-eslint/no-explicit-any */
"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { CloseEvent, ComparisonRow, Investigation, MissionState, Separation, Step } from "@/lib/types";
import { DecisionTiming, STATUS_LABEL } from "./MissionReadings";
import PanelToggle from "./PanelToggle";
import EventQuestions from "./EventQuestions";
import SeparationChart from "./SeparationChart";

const TOOL_ICON: Record<string, string> = {
  get_satellite_info: "🛰",
  get_trajectory: "〰",
  analyze_close_approach: "⌖",
  simulate_maneuver: "⟳",
  compare_maneuvers: "⚖",
  generate_incident_report: "▤",
};

function summarize(s: Step): string {
  const o = s.output ?? {};
  if (s.status === "error") return `⚠ ${o.error ?? "error"}`;
  switch (s.tool) {
    case "get_satellite_info":
      return `${o.name} · ${String(o.kind).replace("_", " ").toLowerCase()} · ${o.altitude_km} km · ${o.maneuverable ? "maneuverable" : "cannot maneuver"} · ${o.data_label}`;
    case "get_trajectory":
      return `${o.satellite_id}: altitude ${o.altitude_km?.min}–${o.altitude_km?.max} km over ${o.n_samples} samples`;
    case "analyze_close_approach": {
      const c = o.closest;
      return c
        ? `closest ${c.miss_km} km at ${String(c.tca_utc).slice(11, 19)}Z · ${c.rel_speed_km_s} km/s · ${c.miss_over_sigma}σ · ${o.approaches_within_50km.length} pass(es) < 50 km`
        : "no approach within 50 km";
    }
    case "simulate_maneuver":
      return `${o.maneuver_id}: ${o.direction} ${o.delta_v_m_s} m/s, T−${o.burn_lead_time_min} min → miss ${o.new_miss_km} km${o.secondary_conjunctions?.length ? ` · ⚠ ${o.secondary_conjunctions.length} new conjunction(s)` : " · no new conjunctions"}`;
    case "compare_maneuvers":
      return `best option: ${o.best_option ?? "none acceptable"} (target ≥ ${o.target_miss_km} km, ${o.options?.length} options)`;
    case "generate_incident_report":
      return `${o.report_id} compiled from ${o.evidence_count} evidence items`;
    default:
      return "";
  }
}

function StepRow({ s }: { s: Step }) {
  const [open, setOpen] = useState(false);
  if (s.kind === "thought") {
    return (
      <li className="step-in border-l-2 border-line pl-3 text-xs italic leading-relaxed text-ink-2">
        <span className="label not-italic">Agent reasoning</span>
        <p className="line-clamp-4 whitespace-pre-wrap">{s.output?.text}</p>
      </li>
    );
  }
  if (s.kind === "system" || s.kind === "final") {
    if (s.kind === "final") return null;
    return (
      <li className={`step-in text-xs ${s.status === "error" ? "text-amber" : "text-muted"}`}>
        ⓘ {s.output?.message}
      </li>
    );
  }
  const actor = s.input?.actor;
  return (
    <li className="step-in">
      <button
        onClick={() => setOpen(!open)}
        className={`w-full rounded-lg border px-2.5 py-1.5 text-left ${s.status === "error" ? "border-amber/40 bg-amber/5" : "border-line bg-panel-2/60 hover:border-line-strong"}`}
        aria-expanded={open}
      >
        <div className="flex items-center gap-2 text-xs">
          <span className="w-4 text-center">{TOOL_ICON[s.tool ?? ""] ?? "•"}</span>
          <span className="font-mono font-semibold text-ink">{s.tool}</span>
          {s.evidence_id && <span className="rounded bg-accent/15 px-1.5 font-mono text-[10px] text-accent">{s.evidence_id}</span>}
          <span className="ml-auto text-[10px] text-muted">{actor === "system" ? "system" : ""} {s.duration_ms.toFixed(0)} ms</span>
        </div>
        <div className="mt-0.5 pl-6 text-[11px] text-ink-2">{summarize(s)}</div>
      </button>
      {open && (
        <pre className="scroll-thin mt-1 max-h-60 overflow-auto rounded-lg border border-line bg-black/30 p-2 font-mono text-[10px] text-ink-2">
          {JSON.stringify({ input: s.input, output: s.output }, null, 2)}
        </pre>
      )}
    </li>
  );
}

const STATUS_STYLE: Record<string, string> = {
  queued: "bg-watch/15 text-watch",
  running: "bg-watch/15 text-watch",
  awaiting_approval: "bg-amber/15 text-amber",
  approved: "bg-ok/15 text-ok",
  rejected: "bg-red/15 text-red",
  failed: "bg-red/15 text-red",
};
const ACTION_LABEL = {
  PREPARE_MANEUVER_FOR_APPROVAL: "Prepare maneuver for approval",
  MONITOR_AND_REQUEST_TRACKING: "Monitor & request tracking",
  NO_ACTION: "No action",
};
const RISK_STYLE = { HIGH: "text-red", ELEVATED: "text-amber", LOW: "text-ok" };

export default function AgentPanel({ inv, event, events, investigations, onSelectEvent, onReviewEvent, reviewRequest, onStart, onOpenReport, onDecided, llmAvailable, thresholds, onShowManeuver }: {
  inv: Investigation | null; event: CloseEvent | null; onStart: (eventId: string) => void; onOpenReport: () => void;
  events: CloseEvent[]; investigations: MissionState["investigations"]; onSelectEvent: (id: string) => void;
  onReviewEvent: (id: string) => void; reviewRequest: { eventId: string } | null;
  onDecided: () => void; llmAvailable: boolean; thresholds?: MissionState["thresholds_km"];
  onShowManeuver?: (investigationId: string, maneuverId: string) => void;
}) {
  const approval = useRef<HTMLDetailsElement>(null);
  const details = useRef<HTMLDetailsElement>(null);
  const [chatCollapsed, setChatCollapsed] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sep, setSep] = useState<Separation | null>(null);
  const [headlineOpen, setHeadlineOpen] = useState(false);

  const comparison = inv?.report?.maneuver_comparison as { options: ComparisonRow[]; best_option: string | null; target_miss_km: number } | null | undefined;
  const rec = inv?.recommendation ?? null;
  const featured = rec?.recommended_maneuver_id || comparison?.best_option || "";
  const running = inv && (inv.status === "running" || inv.status === "queued");

  const recommended = comparison?.options.find((option) => option.option_id === rec?.recommended_maneuver_id && option.option_id !== "NO_MANEUVER");
  const awaitingApproval = inv?.status === "awaiting_approval";

  useEffect(() => {
    const panel = approval.current;
    if (!panel || !awaitingApproval || reviewRequest?.eventId !== event?.id) return;
    panel.open = true;
    panel.querySelector("summary")?.focus({ preventScroll: true });
    panel.scrollIntoView({ block: "nearest" });
  }, [awaitingApproval, event?.id, reviewRequest]);

  useEffect(() => {
    if (!inv || !event || running || inv.event_id !== event.id) {
      setSep(null);
      return;
    }
    let live = true;
    api.separation(event.id, featured ? inv.id : undefined, featured || undefined).then((d) => live && setSep(d)).catch(() => live && setSep(null));
    return () => {
      live = false;
    };
  }, [inv?.id, inv?.status, event?.id, featured, running]); // eslint-disable-line react-hooks/exhaustive-deps

  const claims = inv?.validation?.claims ?? [];
  const steps = useMemo(() => inv?.steps ?? [], [inv]);
  const toolCount = steps.filter((s) => s.kind === "tool").length;

  async function decide(d: "approve" | "reject") {
    if (!inv) return;
    setBusy(true);
    setErr(null);
    try {
      await api.decide(inv.id, d, note);
      onDecided();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
    <aside className="panel flex min-h-0 flex-col p-3">
      <h2 className="px-1 text-base font-semibold">Collision Assistant</h2>

      {event && (
        <section className="mt-2 rounded-lg border border-line bg-panel-2/50 p-2.5 text-xs">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className={`font-semibold ${event.severity === "RED" ? "text-red" : event.severity === "AMBER" ? "text-amber" : "text-watch"}`}>{event.severity}</span>
            <span className="font-mono text-muted">{event.id}</span>
            <span className="text-ink-2">{inv ? STATUS_LABEL[inv.status] : "Investigation unavailable"}</span>
          </div>
          {rec && (
            <button type="button" onClick={() => setHeadlineOpen((v) => !v)} title={headlineOpen ? "Collapse" : "Show full recommendation"}
              className={`mt-2 w-full text-left text-[12px] leading-snug text-ink-2 hover:text-ink ${headlineOpen ? "" : "line-clamp-4"}`}>
              {rec.headline}
            </button>
          )}
          {recommended && (
            <dl className="mt-2 grid grid-cols-3 gap-1.5 text-center">
              {[
                ["Predicted miss", `${recommended.predicted_miss_km.toFixed(2)} km`],
                ["Δv", `${recommended.delta_v_m_s} m/s`],
                ["New alerts", String(recommended.new_conjunctions)],
              ].map(([label, value]) => (
                <div key={label} className="rounded-md border border-line/70 bg-bg/40 px-1 py-1.5">
                  <dt className="text-[10px] text-muted">{label}</dt>
                  <dd className="text-[13px] font-semibold tabular-nums text-ink">{value}</dd>
                </div>
              ))}
            </dl>
          )}
          <DecisionTiming event={event} option={recommended} />
        </section>
      )}

      {!event && (
        <div className="mt-6 text-center text-sm text-muted">
          <div className="mb-2 text-3xl">⌖</div>
          No close-approach event selected.
          <p className="mt-1 text-xs">The agent starts automatically when monitoring flags a RED or AMBER event.</p>
        </div>
      )}

      {event && (
        <details ref={details} className="mt-2 rounded-lg border border-line bg-panel-2/50 p-2">
          <summary className="cursor-pointer text-xs text-ink-2">
            <span className="font-medium text-ink">Investigation details</span>
            <span className="ml-2 whitespace-nowrap font-mono text-muted">{event.id}</span>
          </summary>
          <div className="scroll-thin max-h-72 overflow-y-auto overflow-x-hidden">
            <div className="mt-2 text-xs text-muted">{llmAvailable ? "AI agent available" : "Rule-based planner"}</div>
            <div id="event-screening-details" className="mt-2 rounded-lg border border-line bg-panel-2/60 p-2 text-xs">
              <div className="flex items-center gap-2">
                <span className="font-mono text-ink">{event.id}</span>
                <span className={`font-semibold ${event.severity === "RED" ? "text-red" : event.severity === "AMBER" ? "text-amber" : "text-watch"}`}>
                  {event.severity === "RED" ? "▲" : event.severity === "AMBER" ? "◆" : "●"} {event.severity}
                </span>
                <span className="ml-auto text-muted">{event.data_label}</span>
              </div>
              <div className="mt-1 text-ink-2">{event.primary_name} × {event.secondary_name}</div>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
                <div><div className="label">Miss</div><div className="whitespace-nowrap text-sm font-semibold tabular-nums">{event.miss_km.toFixed(3)}<span className="ml-0.5 text-[10px] text-ink-2">km</span></div></div>
                <div><div className="label">Rel. speed</div><div className="whitespace-nowrap text-sm font-semibold tabular-nums">{event.rel_speed_km_s.toFixed(2)}<span className="ml-0.5 text-[10px] text-ink-2">km/s</span></div></div>
                <div><div className="label">TCA</div><div className="whitespace-nowrap text-sm font-semibold tabular-nums">{event.tca_utc.slice(11, 19)}<span className="ml-0.5 text-[10px] text-ink-2">Z</span></div></div>
              </div>
              {!inv && (
                <button onClick={() => onStart(event.id)} className="mt-2 w-full rounded-md bg-accent/90 py-1.5 text-xs font-semibold text-black hover:bg-accent">
                  Start investigation
                </button>
              )}
            </div>
          {inv && (
            <div className="mt-3 space-y-3">
              <div className="flex items-center gap-2 text-xs">
                <span className="font-mono">{inv.id}</span>
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${STATUS_STYLE[inv.status]}`}>{inv.status.replace("_", " ").toUpperCase()}</span>
                <span className="text-muted">{inv.mode === "llm" ? inv.model : inv.mode === "llm+fallback" ? "LLM → fallback" : "deterministic planner"}</span>
                <span className="ml-auto text-muted">{toolCount} tool calls</span>
              </div>

              <ol className="space-y-1.5">
                {steps.map((s) => <StepRow key={s.idx} s={s} />)}
                {running && <li className="animate-pulse text-xs text-watch">● Agent is working…</li>}
              </ol>

              {inv.status === "failed" && <div className="rounded-lg border border-red/40 bg-red/10 p-2 text-xs text-red">Investigation failed: {inv.error}</div>}

              {rec && (
                <section className="rounded-xl border border-line-strong bg-panel-2/80 p-3">
                  <div className="label">Recommendation · for human review</div>
                  <h3 className="mt-1 text-base font-semibold leading-snug">{rec.headline}</h3>
                  <div className="mt-1 flex flex-wrap gap-2 text-xs">
                    <span className={`font-semibold ${RISK_STYLE[rec.risk_level]}`}>Risk: {rec.risk_level}</span>
                    <span className="text-ink-2">Action: {ACTION_LABEL[rec.recommended_action]}</span>
                    {rec.recommended_maneuver_id && <span className="font-mono text-accent">{rec.recommended_maneuver_id}</span>}
                  </div>
                  <ul className="mt-2 space-y-1.5">
                    {(claims.length ? claims : rec.rationale.map((r) => ({ ...r, status: "check" as const, unverified_numbers: [] }))).map((c, i) => (
                      <li key={i} className="flex gap-2 text-xs leading-relaxed">
                        <span title={c.status === "verified" ? "All numbers match cited tool output" : `Check: ${c.unverified_numbers.join(", ") || "no evidence cited"}`}
                          className={c.status === "verified" ? "text-ok" : "text-amber"}>
                          {c.status === "verified" ? "✔" : "⚠"}
                        </span>
                        <span className="text-ink-2">
                          {c.claim}{" "}
                          {c.evidence.map((e) => <span key={e} className="ml-0.5 rounded bg-accent/15 px-1 font-mono text-[10px] text-accent">{e}</span>)}
                        </span>
                      </li>
                    ))}
                  </ul>
                  {inv.validation && !inv.validation.valid && (
                    <div className="mt-2 rounded border border-red/40 bg-red/10 p-1.5 text-[11px] text-red">Validation errors: {inv.validation.errors.join("; ")}</div>
                  )}
                  {inv.validation && inv.validation.warnings.length > 0 && (
                    <div className="mt-2 text-[11px] text-amber">⚠ {inv.validation.warnings.join(" · ")}</div>
                  )}
                </section>
              )}

              {comparison && comparison.options.length > 1 && (
                <section>
                  <div id="event-maneuver-comparison" className="label mb-1">Maneuver comparison · target ≥ {comparison.target_miss_km} km</div>
                  <div className="overflow-x-auto rounded-lg border border-line">
                    <table className="w-full text-[11px] tabular-nums">
                      <thead className="bg-panel-2 text-muted">
                        <tr>
                          <th className="px-2 py-1 text-left font-medium">Option</th>
                          <th className="px-2 py-1 text-left font-medium">Burn</th>
                          <th className="px-2 py-1 text-right font-medium">ΔV m/s</th>
                          <th className="px-2 py-1 text-right font-medium">Miss km</th>
                          <th className="px-2 py-1 text-center font-medium">OK</th>
                        </tr>
                      </thead>
                      <tbody>
                        {comparison.options.map((r) => (
                          <tr key={r.option_id} className={`border-t border-line ${r.option_id === featured ? "bg-accent/10" : ""}`}>
                            <td className="px-2 py-1 font-mono">{r.option_id === "NO_MANEUVER" ? "None" : r.option_id}</td>
                            <td className="px-2 py-1 text-ink-2">{r.direction ? `${r.direction} T−${r.burn_lead_time_min}m` : "—"}</td>
                            <td className="px-2 py-1 text-right">{r.delta_v_m_s.toFixed(3)}</td>
                            <td className="px-2 py-1 text-right">{r.predicted_miss_km.toFixed(3)}</td>
                            <td className="px-2 py-1 text-center">{r.acceptable ? <span className="text-ok">✔</span> : r.new_conjunctions ? <span className="text-amber" title="creates new conjunction">⚠</span> : <span className="text-muted">✕</span>}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}

              {sep && event && (
                <SeparationChart
                  data={sep}
                  targetKm={comparison?.target_miss_km}
                  baselineMiss={event.miss_km}
                  maneuverMiss={inv.maneuvers.find((m) => m.maneuver_id === sep.maneuver_id)?.new_miss_km}
                />
              )}

              {inv.report && (
                <button onClick={onOpenReport} className="w-full rounded-md border border-line-strong py-1.5 text-xs hover:bg-panel-2">
                  ▤ Open full incident report
                </button>
              )}

              {inv.decision && (
                <section className={`rounded-xl border p-3 text-xs ${inv.decision.decision === "approve" ? "border-ok/40 bg-ok/5" : "border-red/40 bg-red/5"}`}>
                  <div className="label">Operator decision</div>
                  <div className="mt-1 font-semibold">{inv.decision.decision === "approve" ? "✔ Approved" : "✕ Rejected"} by {inv.decision.operator}</div>
                  {inv.decision.note && <div className="text-ink-2">“{inv.decision.note}”</div>}
                  <div className="mt-1 text-[11px] text-muted">{inv.decision.decided_at} · {inv.decision.effect}</div>
                </section>
              )}
            </div>
          )}
          </div>
        </details>
      )}

      {awaitingApproval && (
        <details key={inv.id} ref={approval} className="mt-2 rounded-xl border border-amber/40 bg-amber/5 p-3">
          <summary className="cursor-pointer text-sm font-semibold text-amber">Review pending decision</summary>
          <p className="mt-1 text-[11px] text-ink-2">Approving records the decision for audit. OrbitalGuard has no uplink and cannot command any spacecraft.</p>
          <div className="mt-2 flex gap-2">
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" aria-label="Decision note" maxLength={500}
              className="w-full rounded-md border border-line bg-panel-2 px-2 py-1 text-xs outline-none focus:border-line-strong" />
          </div>
          <div className="mt-2 flex gap-2">
            <button disabled={busy} onClick={() => decide("approve")} className="flex-1 rounded-md bg-ok/85 py-1.5 text-xs font-semibold text-black hover:bg-ok disabled:opacity-50">Record approval</button>
            <button disabled={busy} onClick={() => decide("reject")} className="flex-1 rounded-md border border-red/60 py-1.5 text-xs font-semibold text-red hover:bg-red/10 disabled:opacity-50">Record rejection</button>
          </div>
          {err && <p className="mt-1 text-[11px] text-red">{err}</p>}
        </details>
      )}

    </aside>

    <aside className="panel mission-group-chat flex min-h-0 flex-col p-3" aria-label="Agent Group Chat" data-collapsed={chatCollapsed}>
      <div className="flex items-center justify-between gap-2">
        <h2 className="px-1 text-base font-semibold">Agent Group Chat</h2>
        <PanelToggle label="Agent Group Chat" target="agent-group-chat-content" collapsed={chatCollapsed} onToggle={() => setChatCollapsed(!chatCollapsed)} />
      </div>
      <div id="agent-group-chat-content" className={chatCollapsed ? "hidden" : "contents"}>
      {event && (event.severity === "RED" || event.severity === "AMBER") && (
        <EventQuestions key={event.scenario_id} event={event} events={events} investigations={investigations} onSelectEvent={onSelectEvent} onReviewEvent={onReviewEvent} investigation={inv} thresholds={thresholds}
          options={comparison?.options ?? []} optionId={featured} onShowManeuver={onShowManeuver} onEvidence={(id) => {
            if (details.current) details.current.open = true;
            document.getElementById(id)?.scrollIntoView({ block: "nearest", inline: "nearest" });
          }} />
      )}
      {(!event || event.severity === "WATCH") && (
        <div className="mt-3 space-y-2 px-1 text-xs text-ink-2">
          <p>Select a RED or AMBER alert to enter the group chat.</p>
          {events.filter((alert) => alert.severity !== "WATCH").map((alert) => (
            <button key={alert.id} type="button" onClick={() => onSelectEvent(alert.id)}
              className="block w-full rounded-md border border-line px-2 py-1.5 text-left hover:border-accent/60">
              {alert.severity} · {alert.primary_name} × {alert.secondary_name}
            </button>
          ))}
        </div>
      )}
      </div>
    </aside>
    </>
  );
}
