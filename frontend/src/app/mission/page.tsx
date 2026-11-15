"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AgentPanel from "@/components/AgentPanel";
import { OrbitalReadings, TrackingSummary } from "@/components/MissionReadings";
import ObjectPanel from "@/components/ObjectPanel";
import ReportModal from "@/components/ReportModal";
import TimelineBar from "@/components/TimelineBar";
import { api } from "@/lib/api";
import { clock, seek } from "@/lib/clock";
import "../mission.css";
import type { Investigation, MissionState, Trajectories } from "@/lib/types";

const Globe = dynamic(() => import("@/components/Globe"), {
  ssr: false,
  loading: () => <div className="grid h-full place-items-center text-sm text-muted">Initialising 3D view…</div>,
});

const SEV_RANK = { RED: 0, AMBER: 1, WATCH: 2 } as const;

export default function MissionPage() {
  const [state, setState] = useState<MissionState | null>(null);
  const [traj, setTraj] = useState<Trajectories | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeEventId, setActiveEventId] = useState<string | null>(null);
  const [inv, setInv] = useState<Investigation | null>(null);
  const [loading, setLoading] = useState<string | null>("Connecting to mission backend…");
  const [error, setError] = useState<string | null>(null);
  const catalog = useRef<HTMLDialogElement>(null);
  const [demoCallsign, setDemoCallsign] = useState<string | null>(null);
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

  const focusInitialEvent = useCallback((s: MissionState) => {
    const ranked = [...s.events].sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity] || a.tca_s - b.tca_s);
    const ev = ranked.find((event) => event.severity !== "WATCH"
      && s.investigations.find((item) => item.event_id === event.id)?.status === "awaiting_approval")
      ?? ranked[0];
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
    focusInitialEvent(s);
  }, [refreshState, focusInitialEvent]);

  useEffect(() => {
    try {
      setDemoCallsign(sessionStorage.getItem("orbitalguard.demoCallsign"));
    } catch {
      // The dashboard remains available when browser storage is disabled.
    }
    loadAll().catch((e: Error) => setError(e.message)).finally(() => setLoading(null));
  }, [loadAll]);

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

  const selected = state?.objects.find((object) => object.id === selectedId) ?? null;
  function selectObject(id: string) {
    setSelectedId(id);
    const included = (event: NonNullable<typeof activeEvent>) => event.primary_id === id || event.secondary_id === id;
    if (activeEvent && included(activeEvent)) return;
    const next = state?.events.filter(included)
      .sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity] || a.tca_s - b.tca_s)[0];
    setActiveEventId(next?.id ?? null);
  }

  function selectEvent(id: string) {
    setReportOpen(false);
    setActiveEventId(id);
    const event = state?.events.find((item) => item.id === id);
    if (event) setSelectedId(event.primary_id);
  }

  return (
    <main className="mission">
      <header className="mission-header">
        <div className="flex items-center gap-2">
          <span className="grid h-8 w-8 place-items-center rounded-full border border-accent/60 text-accent">◎</span>
          <div className="leading-tight">
            <h1 className="text-xl font-semibold tracking-tight">OrbitalGuard AI</h1>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2 text-[11px] font-semibold">
          {state && (
            <span className={`rounded px-2 py-0.5 ${state.data.source === "SIMULATED" ? "bg-accent/15 text-accent" : "bg-ok/15 text-ok"}`}>{dataLabel}</span>
          )}
          <Link href="/login" className="rounded border border-line px-2 py-0.5 text-ink-2 hover:text-ink">
            {demoCallsign ? `Demo · ${demoCallsign}` : "Operator login"}
          </Link>
          <span className="rounded bg-panel-2 px-2 py-0.5 text-ink-2" title="This system has no command capability">ADVISORY ONLY · NO UPLINK</span>
        </div>
      </header>

      <section className="mission-stage" aria-label="Mission control">
        <div className="mission-scene" aria-label="Interactive orbital view">
          <Globe traj={traj} objects={state?.objects ?? []} selectedId={selectedId} onSelect={selectObject} event={activeEvent} />
        </div>

        <TrackingSummary
          tracked={state?.objects.filter((object) => object.kind === "PAYLOAD").length}
          alerts={state?.events.length}
        />

        <div className="mission-left">
          <button type="button" className="mission-card mission-catalog" onClick={() => catalog.current?.showModal()}>
            <span>{selected?.name ?? "Tracked objects"}</span>
            <span className="mission-reading-note">{selected ? `${selected.id} · Browse objects` : "Browse objects"}</span>
            <span className="mission-catalog-arrow" aria-hidden="true">&#x2197;&#xFE0E;</span>
          </button>
          <OrbitalReadings satellite={selected} trajectory={traj} event={activeEvent} />
        </div>

        <div className="mission-right">
          <AgentPanel
            inv={inv?.event_id === activeEventId ? inv : null}
            event={activeEvent}
            events={state?.events ?? []}
            investigations={state?.investigations ?? []}
            onSelectEvent={selectEvent}
            onStart={startInvestigation}
            onOpenReport={() => setReportOpen(true)}
            onDecided={() => {
              pollInv();
              refreshState();
            }}
            llmAvailable={state?.llm_available ?? false}
            thresholds={state?.thresholds_km}
          />
        </div>

        <div className="mission-bottom">
          {(loading || error) && (
            <p className="mission-status mission-card" role={error && !loading ? "alert" : "status"}>
              {loading ?? error}
            </p>
          )}
          {state && (
            <TimelineBar
              horizon={state.horizon_s}
              epoch={state.epoch_utc}
              events={state.events}
              investigations={state.investigations}
              activeEventId={activeEventId}
              onSelectEvent={selectEvent}
            />
          )}
        </div>
      </section>

      <dialog ref={catalog} className="mission-dialog" aria-labelledby="catalog-title">
        <div className="mission-dialog-header">
          <h2 id="catalog-title">Object catalog</h2>
          <form method="dialog"><button className="mission-close" aria-label="Close object catalog">×</button></form>
        </div>
        <ObjectPanel objects={state?.objects ?? []} events={state?.events ?? []} traj={traj} selectedId={selectedId} onSelect={selectObject} />
      </dialog>

      {reportOpen && inv && inv.event_id === activeEventId && <ReportModal inv={inv} onClose={() => setReportOpen(false)} />}
    </main>
  );
}
