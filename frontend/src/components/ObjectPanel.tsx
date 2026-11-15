"use client";

import { useMemo, useState } from "react";
import { liveKinematics, useClockTime } from "@/lib/clock";
import type { CloseEvent, SatObject, Trajectories } from "@/lib/types";

const KIND_LABEL = { PAYLOAD: "Payload", DEBRIS: "Debris", ROCKET_BODY: "Rocket body" } as const;
const KIND_DOT = { PAYLOAD: "bg-[#7cc4ff]", DEBRIS: "bg-[#9aa6b8]", ROCKET_BODY: "bg-[#d2b98a]" } as const;

function Tile({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <div className="tile px-3 py-2">
      <div className="label">{label}</div>
      <div className="mt-0.5 text-2xl font-semibold tabular-nums leading-tight">
        {value}
        {unit && <span className="ml-1 text-xs font-medium text-ink-2">{unit}</span>}
      </div>
    </div>
  );
}

export default function ObjectPanel({ objects, events, traj, selectedId, onSelect }: {
  objects: SatObject[]; events: CloseEvent[]; traj: Trajectories | null; selectedId: string | null; onSelect: (id: string) => void;
}) {
  const [q, setQ] = useState("");
  const t = useClockTime(4);
  const flagged = useMemo(() => {
    const m = new Map<string, CloseEvent["severity"]>();
    for (const e of events) for (const id of [e.primary_id, e.secondary_id]) if (!m.has(id) || e.severity === "RED") m.set(id, e.severity);
    return m;
  }, [events]);
  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    const l = s ? objects.filter((o) => o.name.toLowerCase().includes(s) || o.id.toLowerCase().includes(s)) : objects;
    return [...l].sort((a, b) => Number(flagged.has(b.id)) - Number(flagged.has(a.id)));
  }, [objects, q, flagged]);
  const sel = objects.find((o) => o.id === selectedId) ?? null;
  const live = sel && traj ? liveKinematics(traj, sel.id, t) : null;

  return (
    <aside className="panel flex min-h-0 flex-col gap-3 p-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Tracked objects</h2>
        <span className="label">{objects.length} total</span>
      </div>
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search name or ID…"
        aria-label="Search tracked objects"
        className="rounded-lg border border-line bg-panel-2 px-3 py-1.5 text-sm outline-none placeholder:text-muted focus:border-line-strong"
      />
      <ul className="scroll-thin min-h-0 flex-1 overflow-y-auto pr-1">
        {list.map((o) => {
          const f = flagged.get(o.id);
          return (
            <li key={o.id}>
              <button
                onClick={() => onSelect(o.id)}
                className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition ${o.id === selectedId ? "bg-panel-2 ring-1 ring-line-strong" : "hover:bg-panel-2/60"}`}
              >
                <span className={`h-2 w-2 shrink-0 rounded-full ${KIND_DOT[o.kind]}`} />
                <span className="min-w-0 flex-1 truncate">{o.name}</span>
                {f && (
                  <span className={`rounded px-1.5 text-[10px] font-semibold ${f === "RED" ? "bg-red/20 text-red" : f === "AMBER" ? "bg-amber/20 text-amber" : "bg-watch/15 text-watch"}`}>
                    {f === "RED" ? "▲ " : f === "AMBER" ? "◆ " : "● "}
                    {f}
                  </span>
                )}
                {o.maneuverable && <span title="Maneuverable" className="text-[10px] text-ok">ΔV</span>}
              </button>
            </li>
          );
        })}
        {list.length === 0 && <li className="px-2 py-4 text-sm text-muted">No objects match “{q}”.</li>}
      </ul>

      {sel ? (
        <div className="flex flex-col gap-2 border-t border-line pt-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-base font-semibold">{sel.name}</span>
              <span className={`rounded px-1.5 text-[10px] font-semibold ${sel.data_label === "REAL" ? "bg-ok/15 text-ok" : "bg-accent/15 text-accent"}`}>{sel.data_label}</span>
            </div>
            <div className="text-xs text-muted">
              {sel.id} · {KIND_LABEL[sel.kind]} · {sel.maneuverable ? "maneuverable" : "not maneuverable"} · {sel.operator}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Tile label="Altitude" value={live ? live.alt.toFixed(1) : sel.altitude_km.toFixed(1)} unit="km" />
            <Tile label="Velocity" value={live ? live.speed.toFixed(3) : sel.speed_km_s.toFixed(3)} unit="km/s" />
            <Tile label="Inclination" value={sel.elements.inclination_deg.toFixed(2)} unit="°" />
            <Tile label="Period" value={sel.elements.period_min.toFixed(1)} unit="min" />
          </div>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
            <dt className="text-muted">Perigee / apogee</dt>
            <dd className="text-right tabular-nums">{sel.elements.perigee_alt_km.toFixed(0)} / {sel.elements.apogee_alt_km.toFixed(0)} km</dd>
            <dt className="text-muted">Eccentricity</dt>
            <dd className="text-right tabular-nums">{sel.elements.eccentricity.toFixed(5)}</dd>
            <dt className="text-muted">RAAN</dt>
            <dd className="text-right tabular-nums">{sel.elements.raan_deg.toFixed(2)}°</dd>
            <dt className="text-muted">TLE epoch (age)</dt>
            <dd className="text-right tabular-nums">{sel.tle_epoch.slice(0, 16).replace("T", " ")} ({sel.tle_age_days_at_scenario_epoch.toFixed(2)} d)</dd>
            {live && (
              <>
                <dt className="text-muted">Latitude (live)</dt>
                <dd className="text-right tabular-nums">{live.lat.toFixed(2)}°</dd>
              </>
            )}
          </dl>
        </div>
      ) : (
        <p className="border-t border-line pt-3 text-xs text-muted">Select an object in the list or on the globe to see its orbit and telemetry.</p>
      )}
    </aside>
  );
}
