"use client";

import { useEffect, useState } from "react";
import type { Health } from "@/lib/types";

const AI_STYLE: Record<Health["llm"]["state"], { cls: string; label: (m: string | null) => string }> = {
  online: { cls: "border-ok/50 bg-ok/10 text-ok", label: (m) => `AI · ${m ?? "LLM"} online` },
  unreachable: { cls: "border-amber/60 bg-amber/10 text-amber", label: () => "AI unreachable · rule-based fallback" },
  auth_error: { cls: "border-red/60 bg-red/10 text-red", label: () => "AI key rejected · rule-based fallback" },
  error: { cls: "border-amber/60 bg-amber/10 text-amber", label: () => "AI error · rule-based fallback" },
  not_configured: { cls: "border-line bg-panel-2 text-ink-2", label: () => "AI off · rule-based planner" },
};

/** Header pills: backend link and real LLM reachability (hover for the reason). */
export function StatusPills({ backendOk, health, onRecheck }: { backendOk: boolean; health: Health | null; onRecheck: () => void }) {
  const ai = health?.llm;
  return (
    <>
      <span role="status" title={backendOk ? "Mission backend connected" : "Mission backend unreachable"}
        className={`rounded border px-2 py-0.5 ${backendOk ? "border-ok/50 bg-ok/10 text-ok" : "border-red/60 bg-red/15 text-red"}`}>
        {backendOk ? "● Backend" : "● Backend offline"}
      </span>
      {ai && backendOk && (
        <button type="button" onClick={onRecheck} title={`${ai.detail}\nChecked ${ai.checked_at.slice(11, 19)} UTC · click to re-check`}
          className={`rounded border px-2 py-0.5 ${AI_STYLE[ai.state].cls}`}>
          {AI_STYLE[ai.state].label(ai.model)}
        </button>
      )}
    </>
  );
}

/** Full-width banner while the backend is unreachable: stale data is labelled, retry is one click away. */
export function ConnectionBanner({ error, lastOkAt, onRetry }: { error: string; lastOkAt: number | null; onRetry: () => void }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const age = lastOkAt ? Math.round((now - lastOkAt) / 1000) : null;
  return (
    <div role="alert" className="mb-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-red/60 bg-[#2a0f14] px-4 py-2.5 text-sm">
      <span className="font-semibold text-red">⛔ Backend connection lost</span>
      <span className="text-xs text-ink-2">
        {age !== null ? `Showing data from ${age} s ago — alerts, investigations and chat are paused.` : "No mission data loaded yet."}
        {" "}Retrying every 3 s.
      </span>
      <span className="text-xs text-muted">{error}</span>
      <button type="button" onClick={onRetry} className="ml-auto rounded-md border border-red/60 px-3 py-1 text-xs font-semibold text-red hover:bg-red/10">
        ↻ Retry now
      </button>
    </div>
  );
}
