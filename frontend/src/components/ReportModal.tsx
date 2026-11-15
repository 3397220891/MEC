/* eslint-disable @typescript-eslint/no-explicit-any */
"use client";

import type { Investigation } from "@/lib/types";

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted">{k}</dt>
      <dd className="tabular-nums text-ink">{v}</dd>
    </>
  );
}

export default function ReportModal({ inv, onClose }: { inv: Investigation; onClose: () => void }) {
  const r = inv.report as any;
  const rec = inv.recommendation;
  if (!r) return null;
  const es = r.event_summary;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" onClick={onClose} role="dialog" aria-modal="true" aria-label="Incident report">
      <div className="panel scroll-thin max-h-full w-full max-w-3xl overflow-y-auto bg-panel-solid p-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start gap-3">
          <div>
            <div className="label">Incident report · {r.data_label} data</div>
            <h2 className="text-xl font-semibold">{r.report_id}</h2>
            <div className="text-xs text-muted">Generated {r.generated_at} · investigation {inv.id} · {inv.mode === "llm" ? inv.model : inv.mode}</div>
          </div>
          <button onClick={onClose} className="ml-auto rounded-md border border-line px-2 py-1 text-xs hover:bg-panel-2" aria-label="Close report">✕ Close</button>
        </div>

        <h3 className="label mt-5">1 · Event summary</h3>
        <dl className="mt-1 grid grid-cols-[180px_1fr] gap-x-4 gap-y-0.5 text-sm">
          <KV k="Event" v={`${es.event_id} (${es.severity})`} />
          <KV k="Primary" v={es.primary} />
          <KV k="Secondary" v={es.secondary} />
          <KV k="Time of closest approach" v={es.tca_utc} />
          <KV k="Predicted miss distance" v={`${es.miss_km} km`} />
          <KV k="Miss components R / I / C" v={`${es.miss_ric_km.radial} / ${es.miss_ric_km.in_track} / ${es.miss_ric_km.cross_track} km`} />
          <KV k="Relative speed" v={`${es.rel_speed_km_s} km/s`} />
          <KV k="Detected by" v={es.detected_by} />
        </dl>

        <h3 className="label mt-5">2 · Objects & data freshness</h3>
        <table className="mt-1 w-full text-xs">
          <thead className="text-muted"><tr><th className="text-left">Object</th><th className="text-left">Source</th><th className="text-right">TLE age</th><th className="text-right">σ at TCA</th><th className="text-right">Maneuverable</th></tr></thead>
          <tbody>
            {r.objects.map((o: any) => (
              <tr key={o.id} className="border-t border-line"><td className="py-1">{o.name} <span className="text-muted">({o.id})</span></td><td>{o.source}</td><td className="text-right">{o.tle_age_days_at_scenario_epoch} d</td><td className="text-right">{o.position_sigma_km_at_tca} km</td><td className="text-right">{o.maneuverable ? "yes" : "no"}</td></tr>
            ))}
          </tbody>
        </table>

        <h3 className="label mt-5">3 · Analyses performed (evidence)</h3>
        <ul className="mt-1 space-y-0.5 font-mono text-[11px] text-ink-2">
          {r.analyses_performed.map((a: any) => (
            <li key={a.evidence_id}><span className="text-accent">{a.evidence_id}</span> {a.tool}({Object.entries(a.input).filter(([k]) => k !== "actor").map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})</li>
          ))}
        </ul>

        {rec && (
          <>
            <h3 className="label mt-5">4 · Recommendation (agent, evidence-cited)</h3>
            <p className="mt-1 text-sm font-semibold">{rec.headline}</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-ink-2">
              {rec.rationale.map((c, i) => <li key={i}>{c.claim} <span className="font-mono text-[11px] text-accent">[{c.evidence.join(", ")}]</span></li>)}
            </ul>
            <h3 className="label mt-4">Uncertainties</h3>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-ink-2">{rec.uncertainties.map((u, i) => <li key={i}>{u}</li>)}</ul>
            <h3 className="label mt-4">Recommended next steps</h3>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-ink-2">{rec.next_steps.map((u, i) => <li key={i}>{u}</li>)}</ul>
          </>
        )}

        <h3 className="label mt-5">5 · Limitations</h3>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-ink-2">{r.limitations.map((l: string, i: number) => <li key={i}>{l}</li>)}</ul>

        <h3 className="label mt-5">6 · Human approval</h3>
        <p className="mt-1 text-sm">
          {inv.decision ? `${inv.decision.decision === "approve" ? "Approved" : "Rejected"} by ${inv.decision.operator} at ${inv.decision.decided_at}.` : "PENDING: awaiting operator decision."}
        </p>
      </div>
    </div>
  );
}
