"use client";

import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Separation } from "@/lib/types";

const BASE = "var(--series-baseline)";
const MNV = "var(--series-maneuver)";

/** baselineMiss / maneuverMiss: refined TCA results from the physics engine (the curve itself is sampled). */
export default function SeparationChart({ data, targetKm, baselineMiss, maneuverMiss }: {
  data: Separation; targetKm?: number; baselineMiss: number; maneuverMiss?: number;
}) {
  const rows = data.baseline.map((p, i) => ({
    dt: p.dt_from_tca_s,
    baseline: p.sep_km,
    maneuvered: data.maneuvered?.[i]?.sep_km,
  }));
  const label = data.maneuver_id ? `With ${data.maneuver_id}` : undefined;
  return (
    <figure>
      <figcaption className="mb-1 text-xs text-ink-2">
        Separation around TCA (km, log scale) · miss {baselineMiss.toFixed(3)} km
        {data.maneuvered && maneuverMiss != null && <> → {maneuverMiss.toFixed(3)} km with {data.maneuver_id}</>}
      </figcaption>
      <div className="h-40">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="#1e3a5c" strokeDasharray="2 4" vertical={false} />
            <XAxis dataKey="dt" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(v) => `${Math.round(v)}s`}
              stroke="#7d93ad" tick={{ fontSize: 10, fill: "#7d93ad" }} tickLine={false} axisLine={{ stroke: "#1e3a5c" }} />
            <YAxis scale="log" domain={["auto", "auto"]} allowDataOverflow tickFormatter={(v) => (v >= 1 ? `${Math.round(v)}` : Number(v).toFixed(2))} stroke="#7d93ad" tick={{ fontSize: 10, fill: "#7d93ad" }} tickLine={false} axisLine={false} width={44} />
            <Tooltip
              contentStyle={{ background: "#0d1e33", border: "1px solid #2c5482", borderRadius: 8, fontSize: 12, color: "#f2f6fb" }}
              labelStyle={{ color: "#b7c7da" }}
              labelFormatter={(v) => `TCA ${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(0)} s`}
              formatter={(v, name) => [`${Number(v).toFixed(3)} km`, name === "baseline" ? "No maneuver" : label]}
              cursor={{ stroke: "#7d93ad", strokeDasharray: "3 3" }}
            />
            {targetKm && <ReferenceLine y={targetKm} stroke="#7d93ad" strokeDasharray="4 4" label={{ value: `target ${targetKm} km`, fill: "#b7c7da", fontSize: 10, position: "insideTopRight" }} />}
            <Legend wrapperStyle={{ fontSize: 11, color: "#b7c7da" }} formatter={(v) => (v === "baseline" ? "No maneuver" : label)} iconType="plainline" />
            <Line type="monotone" dataKey="baseline" stroke={BASE} strokeWidth={2} dot={false} isAnimationActive={false} activeDot={{ r: 4, stroke: "#0d1e33", strokeWidth: 2 }} />
            {data.maneuvered && <Line type="monotone" dataKey="maneuvered" stroke={MNV} strokeWidth={2} dot={false} isAnimationActive={false} activeDot={{ r: 4, stroke: "#0d1e33", strokeWidth: 2 }} />}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
