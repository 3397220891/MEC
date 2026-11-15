"use client";

import { clock, fmtDuration, liveKinematics, seek, useClockTime, utcAt } from "@/lib/clock";
import type { CloseEvent, ComparisonRow, InvestigationSummary, SatObject, Trajectories } from "@/lib/types";

function Reading({ label, value, unit, tone, children }: {
  label: string;
  value: string | number;
  unit?: string;
  tone?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="mission-card mission-reading">
      <div className="mission-reading-label">{label}</div>
      <div className={`mission-value ${tone ?? ""}`}>
        {value}{unit && <span className="mission-unit">{unit}</span>}
      </div>
      {children}
    </div>
  );
}

const riskTone = { RED: "mission-red", AMBER: "mission-amber", WATCH: "mission-watch" };

export function TrackingSummary({ tracked, alerts }: { tracked?: number; alerts?: number }) {
  return (
    <div className="mission-counts" aria-label="Tracking summary">
      <Reading label="Tracked satellites" value={tracked ?? "—"} />
      <Reading label="Conjunction alerts" value={alerts ?? "—"} tone={alerts ? "mission-amber" : undefined} />
    </div>
  );
}

export function OrbitalReadings({ satellite, trajectory, event }: {
  satellite: SatObject | null;
  trajectory: Trajectories | null;
  event: CloseEvent | null;
}) {
  const t = useClockTime(4);
  const live = satellite && trajectory ? liveKinematics(trajectory, satellite.id, t) : null;
  const remaining = event ? event.tca_s - t : 0;

  return (
    <section className="mission-telemetry" aria-label="Orbital telemetry">
      <Reading label="Satellite altitude" value={(live?.alt ?? satellite?.altitude_km)?.toFixed(1) ?? "—"} unit="km" />
      <Reading label="Orbital velocity" value={(live?.speed ?? satellite?.speed_km_s)?.toFixed(3) ?? "—"} unit="km/s" />
      <Reading label="Closest approach" value={event?.miss_km.toFixed(3) ?? "—"} unit="km">
        {event && <p className="mission-reading-note">{remaining > 0 ? `TCA in ${fmtDuration(remaining).slice(2)}` : `Passed · ${event.tca_utc.slice(11, 19)} UTC`}</p>}
      </Reading>
      <Reading label="Collision risk" value={event?.severity ?? "—"} tone={event ? riskTone[event.severity] : undefined} />
    </section>
  );
}

export const STATUS_LABEL: Record<string, string> = {
  queued: "Queued",
  running: "Agent investigating…",
  awaiting_approval: "Awaiting operator approval",
  approved: "Approved (recorded)",
  rejected: "Rejected",
  failed: "Failed",
};

export function AlertQueue({ events, investigations, selectedId, onSelect, onReview }: {
  events: CloseEvent[];
  investigations: InvestigationSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onReview: (id: string) => void;
}) {
  const pending = events.filter((event) => investigations.find((item) => item.event_id === event.id)?.status === "awaiting_approval");
  return (
    <section className="mission-alert-queue mission-card" aria-labelledby="mission-alert-heading">
      <div className="shrink-0">
        <h2 id="mission-alert-heading" className="text-sm font-semibold">Alert queue</h2>
        <p className="text-xs text-muted">{pending.length} awaiting review</p>
      </div>
      {events.length === 0 && <p className="text-xs text-muted">No close-approach alerts.</p>}
      <ul className="scroll-thin flex min-w-0 flex-1 flex-wrap gap-2 overflow-auto">
        {[...pending, ...events.filter((event) => !pending.includes(event))].map((event) => {
          const status = investigations.find((item) => item.event_id === event.id)?.status;
          const needsReview = status === "awaiting_approval";
          return (
            <li key={event.id} className={`flex min-w-0 items-center gap-2 rounded-lg border px-2 py-1 ${event.id === selectedId ? "border-line-strong bg-panel-2" : "border-line"}`}>
              <button type="button" aria-pressed={event.id === selectedId} onClick={() => onSelect(event.id)} className="min-w-0 text-left text-xs">
                <span className={`mr-1.5 font-semibold ${riskTone[event.severity]}`}>{event.severity}</span>
                <span className="font-medium text-ink">{event.primary_name} × {event.secondary_name}</span>
                <span className="block text-muted">{event.miss_km.toFixed(3)} km · TCA {event.tca_utc.slice(11, 19)} UTC · {status ? STATUS_LABEL[status] : "Not investigated"}</span>
              </button>
              {needsReview && <button type="button" onClick={() => onReview(event.id)} aria-label={`Review ${event.primary_name} alert`}
                className="shrink-0 rounded border border-amber/40 px-2 py-1 text-xs font-medium text-amber hover:bg-amber/10">Review</button>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function DecisionTiming({ event, option }: { event: CloseEvent; option?: ComparisonRow }) {
  const t = useClockTime(4);
  const untilTca = event.tca_s - t;
  const lead = option?.burn_lead_time_min;
  const untilBurn = lead == null ? null : untilTca - lead * 60;
  return (
    <dl className="mt-2 space-y-1.5 text-xs text-ink-2">
      <div>
        <dt className="text-muted">Closest approach · mission clock</dt>
        <dd className="tabular-nums">{untilTca > 0 ? `In ${fmtDuration(untilTca).slice(2)}` : "Passed"} · {event.tca_utc.slice(11, 19)} UTC</dd>
      </div>
      {untilBurn !== null && (
        <div>
          <dt className="text-muted">Proposed burn · mission clock</dt>
          <dd className={`tabular-nums ${untilBurn <= 0 ? "text-amber" : ""}`}>
            {untilBurn > 0 ? `In ${fmtDuration(untilBurn).slice(2)}` : "Proposed burn time passed"} · {lead != null && utcAt(event.tca_utc, -lead * 60)} UTC
            {lead != null && (
              <button type="button" className="ml-1.5 text-watch underline underline-offset-2"
                title="Move the mission clock to 2 minutes before the proposed burn and play"
                onClick={() => { seek(event.tca_s - lead * 60 - 120); clock.playing = true; }}>
                ⏮ replay from burn
              </button>
            )}
          </dd>
        </div>
      )}
    </dl>
  );
}
