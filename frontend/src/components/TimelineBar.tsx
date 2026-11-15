"use client";

import { useState } from "react";
import PanelToggle from "./PanelToggle";
import { clock, fmtDuration, seek, useClockTime, utcAt } from "@/lib/clock";
import type { CloseEvent } from "@/lib/types";

const SPEEDS = [1, 10, 60, 300];
const SEV = { RED: "text-red", AMBER: "text-amber", WATCH: "text-watch" } as const;
const SEV_ICON = { RED: "▲", AMBER: "◆", WATCH: "●" } as const;
export default function TimelineBar({ horizon, epoch, events, activeEventId, onSelectEvent }: {
  horizon: number; epoch: string; events: CloseEvent[];
  activeEventId: string | null; onSelectEvent: (id: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const t = useClockTime(10);
  const hours = Math.floor(horizon / 3600);
  const pct = (x: number) => `${(100 * x) / horizon}%`;

  return (
    <section className="panel mission-timeline min-h-0 p-3">
      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h2 className="text-sm font-semibold">Mission timeline</h2>
          <span className="font-mono text-sm tabular-nums text-ink">{utcAt(epoch, t)} UTC · simulation</span>
          <span className="font-mono text-xs tabular-nums text-muted">{fmtDuration(t)}</span>
          <div className={`ml-auto items-center gap-1 ${collapsed ? "hidden" : "flex"}`}>
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
          <div className={collapsed ? "ml-auto" : undefined}>
            <PanelToggle label="Mission timeline" target="mission-time-slider" collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />
          </div>
        </div>
        <div
          id="mission-time-slider"
          hidden={collapsed}
          className="relative h-12 cursor-pointer rounded-lg border border-line bg-panel-2/70"
          onClick={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            seek(((e.clientX - r.left) / r.width) * horizon);
          }}
          onKeyDown={(e) => {
            if (e.target !== e.currentTarget) return;
            const target = { ArrowLeft: t - 60, ArrowRight: t + 60, Home: 0, End: horizon }[e.key];
            if (target === undefined) return;
            e.preventDefault();
            seek(target);
          }}
          tabIndex={0}
          role="slider"
          aria-label="Simulation time"
          aria-valuemin={0}
          aria-valuemax={horizon}
          aria-valuenow={Math.round(t)}
          aria-valuetext={fmtDuration(t)}
        >
          {Array.from({ length: hours + 1 }, (_, h) => (
            <div key={h} className="absolute top-0 h-full border-l border-line" style={{ left: pct(h * 3600) }}>
              <span className={`absolute bottom-0.5 whitespace-nowrap text-[10px] text-muted ${h * 3600 === horizon ? "right-1" : "left-1"}`}>T+{h}h</span>
            </div>
          ))}
          {events.map((e) => (
            <button
              key={e.id}
              aria-pressed={e.id === activeEventId}
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

    </section>
  );
}
