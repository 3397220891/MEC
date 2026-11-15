"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AgentPanel from "@/components/AgentPanel";
import ErrorBoundary from "@/components/ErrorBoundary";
import { ConnectionBanner, StatusPills } from "@/components/SystemStatus";
import Toasts from "@/components/Toasts";
import { AlertQueue, OrbitalReadings, TrackingSummary } from "@/components/MissionReadings";
import ObjectPanel from "@/components/ObjectPanel";
import ReportModal from "@/components/ReportModal";
import TimelineBar from "@/components/TimelineBar";
import { api } from "@/lib/api";
import { clock, seek } from "@/lib/clock";
import { errorText, toast } from "@/lib/toast";
import "../mission.css";
import type { Health, Investigation, ManeuverPlan, MissionState, Trajectories } from "@/lib/types";

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
  const [reviewRequest, setReviewRequest] = useState<{ eventId: string } | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  const [plan, setPlan] = useState<ManeuverPlan | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [lastOkAt, setLastOkAt] = useState<number | null>(null);
  const stageRef = useRef<HTMLElement>(null);
  const leftRef = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);
  const [insets, setInsets] = useState<{ left: number; right: number } | undefined>(undefined);

  // Measure how much of the globe canvas the side panels cover, so Earth is centred in the free space.
  useEffect(() => {
    const stage = stageRef.current, left = leftRef.current, right = rightRef.current;
    if (!stage || !left || !right) return;
    const measure = () => {
      if (window.innerWidth <= 840) return setInsets(undefined); // stacked layout: globe is not overlaid
      const s = stage.getBoundingClientRect(), l = left.getBoundingClientRect(), r = right.getBoundingClientRect();
      setInsets({ left: Math.max(0, l.right - s.left), right: Math.max(0, s.right - r.left) });
    };
    measure();
    const ro = new ResizeObserver(measure);
    [stage, left, right].forEach((el) => ro.observe(el));
    return () => ro.disconnect();
  }, []);

  const refreshState = useCallback(async () => {
    try {
      const s = await api.state();
      setState(s);
      setError(null);
      setLastOkAt(Date.now());
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

  // Backend + real LLM reachability, polled; state changes are announced with a toast.
  const checkHealth = useCallback(async (probe = false) => {
    try {
      const h = await api.health(probe);
      setHealth((prev) => {
        if (prev && prev.llm.state !== h.llm.state) {
          if (h.llm.state === "online") toast("ok", "AI agent back online", h.llm.detail);
          else toast(h.llm.state === "not_configured" ? "info" : "warn", "AI agent unavailable — using the rule-based planner", h.llm.detail);
        }
        return h;
      });
    } catch {
      /* the state poll reports backend outages */
    }
  }, []);
  useEffect(() => {
    checkHealth();
    const id = setInterval(() => checkHealth(), 15000);
    return () => clearInterval(id);
  }, [checkHealth]);

  const backendDown = !!error && !loading;
  const wasDown = useRef(false);
  useEffect(() => {
    if (backendDown && !wasDown.current) toast("error", "Backend connection lost", error ?? undefined);
    if (!backendDown && wasDown.current) {
      toast("ok", "Backend reconnected");
      checkHealth(true);
      if (!traj) loadAll().catch(() => undefined); // first load never completed: fetch orbits now
    }
    wasDown.current = backendDown;
  }, [backendDown, error, checkHealth, traj, loadAll]);

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

  // Once the agent has finished, fetch its maneuver plan (burn points at T−N + maneuvered path) for the globe.
  const planKey = inv && inv.status !== "queued" && inv.status !== "running" && inv.recommendation ? inv.id : null;
  useEffect(() => {
    if (!planKey) return setPlan(null);
    let live = true;
    api.plan(planKey).then((p) => live && setPlan(p)).catch((e) => {
      if (!live) return;
      setPlan(null);
      toast("warn", "Maneuver plan could not be drawn on the globe", errorText(e));
    });
    return () => { live = false; };
  }, [planKey]);

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
      toast("error", `Could not start the investigation for ${eventId}`, errorText(e));
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
    setReviewRequest(null);
    setActiveEventId(id);
    const event = state?.events.find((item) => item.id === id);
    if (event) setSelectedId(event.primary_id);
  }

  function reviewEvent(id: string) {
    selectEvent(id);
    setReviewRequest({ eventId: id });
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
          <StatusPills backendOk={!backendDown} health={health} onRecheck={() => checkHealth(true)} />
          <span className="rounded bg-panel-2 px-2 py-0.5 text-ink-2" title="This system has no command capability">ADVISORY ONLY · NO UPLINK</span>
        </div>
      </header>

      {backendDown && <ConnectionBanner error={error!} lastOkAt={lastOkAt} onRetry={() => { (state ? refreshState() : loadAll()).catch(() => undefined); }} />}

      {state && <AlertQueue events={[...state.events].sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity] || a.tca_s - b.tca_s)}
        investigations={state.investigations} selectedId={activeEventId} onSelect={selectEvent} onReview={reviewEvent} />}

      <section ref={stageRef} className="mission-stage" aria-label="Mission control">
        <div className="mission-scene" aria-label="Interactive orbital view">
          <ErrorBoundary name="3D view">
          <Globe traj={traj} objects={state?.objects ?? []} selectedId={selectedId} onSelect={selectObject} event={activeEvent} plan={plan} insets={insets} />
          </ErrorBoundary>
        </div>

        <TrackingSummary
          tracked={state?.objects.filter((object) => object.kind === "PAYLOAD").length}
          alerts={state?.events.length}
        />

        <div ref={leftRef} className="mission-left">
          <button type="button" className="mission-card mission-catalog" onClick={() => catalog.current?.showModal()}>
            <span>{selected?.name ?? "Tracked objects"}</span>
            <span className="mission-reading-note">{selected ? `${selected.id} · Browse objects` : "Browse objects"}</span>
            <span className="mission-catalog-arrow" aria-hidden="true">&#x2197;&#xFE0E;</span>
          </button>
          <OrbitalReadings satellite={selected} trajectory={traj} event={activeEvent} />
        </div>

        <div ref={rightRef} className="mission-right">
          <AgentPanel
            inv={inv?.id === activeInvId && inv?.event_id === activeEventId ? inv : null}
            event={activeEvent}
            events={state?.events ?? []}
            investigations={state?.investigations ?? []}
            onSelectEvent={selectEvent}
            onReviewEvent={reviewEvent}
            reviewRequest={reviewRequest}
            onStart={startInvestigation}
            onOpenReport={() => setReportOpen(true)}
            onDecided={() => {
              pollInv();
              refreshState();
            }}
            llmAvailable={state?.llm_available ?? false}
            thresholds={state?.thresholds_km}
            onShowManeuver={(invId, mid) => {
              api.plan(invId, mid).then((p) => { setPlan(p); toast("info", `Showing ${mid} on the globe`, p.options.find((o) => o.maneuver_id === mid)?.label); })
                .catch((e) => toast("error", `Could not draw ${mid} on the globe`, errorText(e)));
            }}
          />
        </div>

        <div className="mission-bottom">
          {loading && <p className="mission-status mission-card" role="status">{loading}</p>}
          {state && (
            <TimelineBar
              horizon={state.horizon_s}
              epoch={state.epoch_utc}
              events={state.events}
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

      <Toasts />
      {reportOpen && inv && inv.event_id === activeEventId && <ReportModal inv={inv} onClose={() => setReportOpen(false)} />}
    </main>
  );
}
