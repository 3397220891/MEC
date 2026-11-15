"use client";
import { useEffect, useState } from "react";
import type { Trajectories } from "./types";

/** Mutable simulation clock shared by the 3D scene (60 fps) and the UI (sampled at 5 Hz). */
export const clock = {
  t: 0, // seconds since scenario epoch
  speed: 60, // sim seconds per real second
  playing: true,
  horizon: 21600,
};

export function seek(t: number) {
  clock.t = Math.max(0, Math.min(clock.horizon, t));
}

export function useClockTime(hz = 5): number {
  const [t, setT] = useState(clock.t);
  useEffect(() => {
    const id = setInterval(() => setT(clock.t), 1000 / hz);
    return () => clearInterval(id);
  }, [hz]);
  return t;
}

export const R_EARTH_KM = 6378.135;
export const OMEGA_EARTH = 7.2921159e-5;

/** Linear interpolation of a TEME position (km) at time t. Returns null if propagation failed. */
export function positionAt(traj: Trajectories, id: string, t: number, out: [number, number, number]): boolean {
  const arr = traj.positions[id];
  if (!arr) return false;
  const f = Math.max(0, Math.min(traj.n - 1.0001, t / traj.step_s));
  const i = Math.floor(f);
  const w = f - i;
  for (let k = 0; k < 3; k++) {
    const a = arr[i * 3 + k];
    const b = arr[(i + 1) * 3 + k];
    if (a == null || b == null) return false;
    out[k] = a + (b - a) * w;
  }
  return true;
}

export function liveKinematics(traj: Trajectories, id: string, t: number) {
  const p0: [number, number, number] = [0, 0, 0];
  const p1: [number, number, number] = [0, 0, 0];
  if (!positionAt(traj, id, t, p0) || !positionAt(traj, id, t + 1, p1)) return null;
  const r = Math.hypot(...p0);
  const v = Math.hypot(p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]);
  const lat = (Math.atan2(p0[2], Math.hypot(p0[0], p0[1])) * 180) / Math.PI;
  return { alt: r - R_EARTH_KM, speed: v, lat };
}

export function utcAt(epochIso: string, t: number): string {
  return new Date(new Date(epochIso).getTime() + t * 1000).toISOString().slice(11, 19);
}

export function fmtDuration(s: number): string {
  const sign = s < 0 ? "-" : "+";
  const a = Math.abs(Math.round(s));
  const h = Math.floor(a / 3600);
  const m = Math.floor((a % 3600) / 60);
  const sec = a % 60;
  return `T${sign}${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}
