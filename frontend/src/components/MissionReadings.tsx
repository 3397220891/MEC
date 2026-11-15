"use client";

import { fmtDuration, liveKinematics, useClockTime } from "@/lib/clock";
import type { CloseEvent, SatObject, Trajectories } from "@/lib/types";

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
        {event && <p className="mission-reading-note">{remaining > 0 ? `TCA in ${fmtDuration(remaining).slice(2)}` : `TCA ${event.tca_utc.slice(11, 19)} UTC`}</p>}
      </Reading>
      <Reading label="Collision risk" value={event?.severity ?? "—"} tone={event ? riskTone[event.severity] : undefined} />
    </section>
  );
}
