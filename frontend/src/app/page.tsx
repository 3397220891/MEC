"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import AgentPanel from "@/components/AgentPanel";
import ObjectPanel from "@/components/ObjectPanel";
import ReportModal from "@/components/ReportModal";
import TimelineBar from "@/components/TimelineBar";
import { api } from "@/lib/api";
import { clock, seek } from "@/lib/clock";
import type { Investigation, MissionState, Scenario, Trajectories } from "@/lib/types";

const Globe = dynamic(() => import("@/components/Globe"), {
  ssr: false,
  loading: () => <div className="grid h-full place-items-center text-sm text-muted">Initialising 3D view…</div>,
});

const SEV_RANK = { RED: 0, AMBER: 1, WATCH: 2 } as const;

export default function Home() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [state, setState] = useState<MissionState | null>(null);
  const [traj, setTraj] = useState<Trajectories | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeEventId, setActiveEventId] = useState<string | null>(null);
  const [inv, setInv] = useState<Investigation | null>(null);
  const [loading, setLoading] = useState<string | null>("Connecting to mission backend…");
  const [error, setError] = useState<string | null>(null);
  const [reportOpen, setReportOpen] = useState(false);

  const refreshState = useCallback(async () => {
    try {
      const s = await api.state();
      setState(s);
      setError(null);
      return s;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  }, []);

  const focusMostSevere = useCallback((s: MissionState) => {
    const ev = [...s.events].sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity] || a.tca_s - b.tca_s)[0];
    setActiveEventId(ev?.id ?? null);
    setSelectedId(ev?.primary_id ?? s.objects[0]?.id ?? null);
    clock.horizon = s.horizon_s;
    seek(ev ? Math.max(0, ev.tca_s - 600) : 0);
    clock.speed = ev ? 10 : 60;
    clock.playing = true;
  }, []);

  const loadAll = useCallback(async () => {
    const s = await refreshState();
    if (!s) return;
    const t = await api.trajectories(s.objects.length > 40 ? 60 : 30);
    setTraj(t);
    focusMostSevere(s);
  }, [refreshState, focusMostSevere]);

  useEffect(() => {
    api.scenarios().then(setScenarios).catch(() => undefined);
    loadAll().finally(() => setLoading(null));
  }, [loadAll]);

  async function loadScenario(id: string) {
    setLoading("Loading scenario, propagating orbits and screening all pairs…");
    setInv(null);
    try {
      await api.loadScenario(id);
      await loadAll();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(null);
    }
  }

  // Keep event / investigation list fresh.
  useEffect(() => {
    const id = setInterval(refreshState, 3000);
    return () => clearInterval(id);
  }, [refreshState]);

  const activeEvent = state?.events.find((e) => e.id === activeEventId) ?? null;
  const activeInvId = useMemo(
    () => state?.investigations.filter((i) => i.event_id === activeEventId).sort((a, b) => b.started_at.localeCompare(a.started_at))[0]?.id ?? null,
    [state, activeEventId],
  );

  const pollInv = useCallback(async () => {
    if (!activeInvId) return setInv(null);
    try {
      setInv(await api.investigation(activeInvId));
    } catch {
      /* transient; next poll retries */
    }
  }, [activeInvId]);

  useEffect(() => {
    pollInv();
    const running = inv?.status === "running" || inv?.status === "queued" || !inv;
    const id = setInterval(pollInv, running ? 1000 : 5000);
    return () => clearInterval(id);
  }, [pollInv, inv?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  async function startInvestigation(eventId: string) {
    try {
      await api.startInvestigation(eventId);
      await refreshState();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const dataLabel = state?.data.source === "SIMULATED" ? "SIMULATED DATA" : state ? `REAL DATA · ${state.data.source}` : "";

  return (
    <main className="grid h-screen grid-rows-[auto_1fr_auto] gap-3 p-3">
      <header className="panel flex flex-wrap items-center gap-3 px-4 py-2">
        <div className="flex items-center gap-2">
          <span className="grid h-8 w-8 place-items-center rounded-full border border-accent/60 text-accent">◎</span>
          <div className="leading-tight">
            <div className="text-base font-semibold tracking-wide">OrbitalGuard AI</div>
            <div className="text-[11px] text-muted">Collision risk investigation &amp; decision support</div>
          </div>
        </div>
        <nav className="ml-4 flex flex-wrap gap-1" aria-label="Scenarios">
          {scenarios.map((s) => (
            <button
              key={s.id}
              onClick={() => loadScenario(s.id)}
              title={s.description}
              disabled={!!loading}
              className={`rounded-lg border px-3 py-1 text-xs transition disabled:opacity-50 ${state?.scenario_id === s.id ? "border-accent/70 bg-accent/15 text-accent" : "border-line text-ink-2 hover:border-line-strong hover:text-ink"}`}
            >
              {s.title}
            </button>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2 text-[11px] font-semibold">
          {state && (
            <span className={`rounded px-2 py-0.5 ${state.data.source === "SIMULATED" ? "bg-accent/15 text-accent" : "bg-ok/15 text-ok"}`}>{dataLabel}</span>
          )}
          <span className="rounded bg-panel-2 px-2 py-0.5 text-ink-2" title="This system has no command capability">ADVISORY ONLY · NO UPLINK</span>
        </div>
      </header>

      <div className="grid min-h-0 grid-cols-[300px_1fr_420px] gap-3">
        <ObjectPanel objects={state?.objects ?? []} events={state?.events ?? []} traj={traj} selectedId={selectedId} onSelect={setSelectedId} />

        <section className="panel relative min-h-0 overflow-hidden">
          {traj && state ? (
            <Globe traj={traj} objects={state.objects} selectedId={selectedId} onSelect={setSelectedId} event={activeEvent} />
          ) : (
            <div className="grid h-full place-items-center text-sm text-muted">{error ? "Backend unavailable" : "Loading orbital data…"}</div>
          )}
          {state && (
            <div className="pointer-events-none absolute left-3 top-3 space-y-1">
              <div className="tile px-3 py-1.5 text-xs">
                <div className="label">Scenario</div>
                <div className="font-semibold">{state.title}</div>
                <div className="text-muted">Epoch {state.epoch_utc.slice(0, 16).replace("T", " ")}Z · horizon {(state.horizon_s / 3600).toFixed(0)} h · screen &lt; {state.thresholds_km.screen} km</div>
              </div>
            </div>
          )}
          {activeEvent && (
            <div className={`pointer-events-none absolute right-3 top-3 tile px-3 py-1.5 text-xs ${activeEvent.severity === "RED" ? "alert-pulse border-red/60" : ""}`}>
              <div className={`label ${activeEvent.severity === "RED" ? "text-red" : activeEvent.severity === "AMBER" ? "text-amber" : "text-watch"}`}>
                {activeEvent.severity === "RED" ? "▲" : activeEvent.severity === "AMBER" ? "◆" : "●"} Close approach · {activeEvent.severity}
              </div>
              <div className="text-2xl font-semibold tabular-nums">{activeEvent.miss_km.toFixed(3)}<span className="ml-1 text-xs text-ink-2">km</span></div>
              <div className="text-muted">TCA {activeEvent.tca_utc.slice(11, 19)}Z</div>
            </div>
          )}
          {loading && <div className="absolute inset-0 grid place-items-center bg-black/50 text-sm text-ink-2">{loading}</div>}
          {error && !loading && (
            <div className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-lg border border-red/50 bg-red/15 px-3 py-1.5 text-xs text-red" role="alert">
              {error}
            </div>
          )}
        </section>

        <AgentPanel
          inv={inv}
          event={activeEvent}
          onStart={startInvestigation}
          onOpenReport={() => setReportOpen(true)}
          onDecided={() => {
            pollInv();
            refreshState();
          }}
          llmAvailable={state?.llm_available ?? false}
        />
      </div>

      <div className="h-40">
        {state && (
          <TimelineBar
            horizon={state.horizon_s}
            epoch={state.epoch_utc}
            events={state.events}
            investigations={state.investigations}
            activeEventId={activeEventId}
            onSelectEvent={(id) => {
              setActiveEventId(id);
              const ev = state.events.find((e) => e.id === id);
              if (ev) setSelectedId(ev.primary_id);
            }}
          />
        )}
      </div>

      {reportOpen && inv && <ReportModal inv={inv} onClose={() => setReportOpen(false)} />}
    </main>
  );
}
