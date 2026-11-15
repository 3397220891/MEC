"use client";

import { clock, fmtDuration, seek, useClockTime, utcAt } from "@/lib/clock";
import type { CloseEvent, InvestigationSummary } from "@/lib/types";

const SPEEDS = [1, 10, 60, 300];
const SEV = { RED: "text-red", AMBER: "text-amber", WATCH: "text-watch" } as const;
const SEV_BG = { RED: "bg-red", AMBER: "bg-amber", WATCH: "bg-watch" } as const;
const SEV_ICON = { RED: "▲", AMBER: "◆", WATCH: "●" } as const;
const STATUS_LABEL: Record<string, string> = {
  queued: "Queued",
  running: "Agent investigating…",
  awaiting_approval: "Awaiting operator approval",
  approved: "Approved (recorded)",
  rejected: "Rejected",
  failed: "Failed",
};

export default function TimelineBar({ horizon, epoch, events, investigations, activeEventId, onSelectEvent }: {
  horizon: number; epoch: string; events: CloseEvent[]; investigations: InvestigationSummary[];
  activeEventId: string | null; onSelectEvent: (id: string) => void;
}) {
  const t = useClockTime(10);
  const hours = Math.floor(horizon / 3600);
  const pct = (x: number) => `${(100 * x) / horizon}%`;

  return (
    <section className="panel grid min-h-0 grid-cols-[1fr_380px] gap-4 p-3">
      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold">Mission timeline</h2>
          <span className="font-mono text-sm tabular-nums text-ink">{utcAt(epoch, t)} UTC</span>
          <span className="font-mono text-xs tabular-nums text-muted">{fmtDuration(t)}</span>
          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={() => (clock.playing = !clock.playing)}
              className="rounded-md border border-line bg-panel-2 px-2.5 py-1 text-xs hover:border-line-strong"
              aria-label={clock.playing ? "Pause simulation" : "Play simulation"}
            >
              {clock.playing ? "❚❚ Pause" : "▶ Play"}
            </button>
            {SPEEDS.map((s) => (
              <button
                key={s}
                onClick={() => (clock.speed = s)}
                className={`rounded-md px-2 py-1 text-xs ${clock.speed === s ? "bg-accent/20 text-accent" : "text-ink-2 hover:text-ink"}`}
              >
                {s}×
              </button>
            ))}
          </div>
        </div>
        <div
          className="relative h-12 cursor-pointer rounded-lg border border-line bg-panel-2/70"
          onClick={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            seek(((e.clientX - r.left) / r.width) * horizon);
          }}
          role="slider"
          aria-label="Simulation time"
          aria-valuemin={0}
          aria-valuemax={horizon}
          aria-valuenow={Math.round(t)}
        >
          {Array.from({ length: hours + 1 }, (_, h) => (
            <div key={h} className="absolute top-0 h-full border-l border-line" style={{ left: pct(h * 3600) }}>
              <span className="absolute bottom-0.5 left-1 text-[10px] text-muted">T+{h}h</span>
            </div>
          ))}
          {events.map((e) => (
            <button
              key={e.id}
              title={`${e.id}: ${e.primary_name} × ${e.secondary_name}, ${e.miss_km.toFixed(3)} km`}
              onClick={(ev) => {
                ev.stopPropagation();
                onSelectEvent(e.id);
                seek(e.tca_s - 300);
              }}
              className={`absolute top-1 -translate-x-1/2 text-base leading-none ${SEV[e.severity]}`}
              style={{ left: pct(e.tca_s) }}
            >
              {SEV_ICON[e.severity]}
            </button>
          ))}
          <div className="pointer-events-none absolute top-0 h-full w-0.5 bg-accent" style={{ left: pct(t) }} />
        </div>
      </div>

      <div className="flex min-h-0 flex-col">
        <div className="mb-1 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Close-approach alerts</h2>
          <span className="label">{events.length} flagged</span>
        </div>
        <ul className="scroll-thin min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
          {events.length === 0 && <li className="text-xs text-muted">No objects inside the screening volume. Nominal operations.</li>}
          {events.map((e) => {
            const inv = investigations.find((i) => i.event_id === e.id);
            return (
              <li key={e.id}>
                <button
                  onClick={() => {
                    onSelectEvent(e.id);
                    seek(e.tca_s - 300);
                  }}
                  className={`flex w-full items-center gap-2 rounded-lg border px-2 py-1 text-left text-xs ${e.id === activeEventId ? "border-line-strong bg-panel-2" : "border-transparent hover:bg-panel-2/60"}`}
                >
                  <span className={`h-2 w-2 shrink-0 rounded-full ${SEV_BG[e.severity]} ${e.severity === "RED" ? "alert-pulse" : ""}`} />
                  <span className={`w-14 shrink-0 font-semibold ${SEV[e.severity]}`}>{e.severity}</span>
                  <span className="min-w-0 flex-1 truncate">
                    {e.primary_name} × {e.secondary_name}
                    <span className="block text-[10px] text-muted">{inv ? STATUS_LABEL[inv.status] ?? inv.status : "Not investigated"}</span>
                  </span>
                  <span className="shrink-0 text-right tabular-nums">
                    {e.miss_km.toFixed(3)} km
                    <span className="block text-[10px] text-muted">{e.tca_utc.slice(11, 19)}Z</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
