import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, fmtKm } from '../api'

const ICON = { tool: '⚙', llm: '◆', system: '■' }

function summarize(step) {
  const r = step.result || {}
  if (step.status === 'error') return r.error ? `${r.error}${r.details ? ': ' + r.details.join('; ') : ''}` : 'error'
  switch (step.name) {
    case 'get_satellite_info':
      return `${r.name} · ${r.state?.altitude_km ?? '?'} km alt · elements ${r.element_age_hours} h old · ${r.source}`
    case 'get_trajectory':
      return `alt ${r.stats?.min_alt_km?.toFixed(1)}–${r.stats?.max_alt_km?.toFixed(1)} km over ${r.window_min?.[1] - r.window_min?.[0]} min`
    case 'analyze_close_approach':
      return `miss ${fmtKm(r.miss_km)} at ${r.tca?.slice(11, 19)}Z · ${r.rel_speed_km_s} km/s · ±${r.indicative_combined_uncertainty_km} km indicative`
    case 'simulate_maneuver':
      return `${r.label}: miss ${fmtKm(r.baseline_miss_km)} → ${fmtKm(r.new_miss_km)} · ${r.dv_magnitude_m_s} m/s · ${r.secondary_conjunctions?.length || 0} secondary`
    case 'compare_maneuvers':
      return r.best_option ? `best ${r.best_option} · ${r.ranked_acceptable.length}/${r.options.length} acceptable` : 'no acceptable option'
    case 'generate_incident_report':
      return `${r.report_id} compiled (${r.options_included} options)`
    case 'submit_recommendation':
      return `accepted · grounding ${r.grounding_check}`
    default:
      return r.text || r.error || r.action || ''
  }
}

function Step({ s }) {
  const [open, setOpen] = useState(false)
  if (s.kind === 'llm') {
    const r = s.result || {}
    if (!r.text) return null
    return (
      <li className="step llm">
        <span className="step-icon">{ICON.llm}</span>
        <div>
          <p className="thought">{r.text}</p>
          <span className="muted small">{r.provider}:{r.model} · {r.cached ? 'replayed from cache' : `${s.duration_ms} ms`}</span>
        </div>
      </li>
    )
  }
  return (
    <li className={`step ${s.status === 'error' ? 'err' : ''}`}>
      <span className="step-icon">{ICON[s.kind] || '•'}</span>
      <div className="step-body">
        <button className="step-head" onClick={() => setOpen(!open)}>
          <span className="ref">E{s.seq}</span>
          <code>{s.name}</code>
          <span className="muted small">{s.duration_ms} ms</span>
        </button>
        <p className="step-sum">{summarize(s)}</p>
        {open && (
          <pre className="json">{JSON.stringify({ args: s.args, result: s.result }, null, 2)}</pre>
        )}
      </div>
    </li>
  )
}

function ComparisonChart({ comparison }) {
  if (!comparison) return null
  const thr = comparison.criteria.min_safe_miss_km
  const data = comparison.options.map((o) => ({
    name: `${o.meets_criteria ? '✓' : '✕'} ${o.label}`,
    miss: o.new_miss_km,
    ok: o.meets_criteria,
    dv: o.dv_magnitude_m_s,
    reasons: o.rejection_reasons.join('; '),
  }))
  return (
    <div className="chart-wrap">
      <h4>Post-maneuver miss distance (km) · threshold {thr} km</h4>
      <ResponsiveContainer width="100%" height={Math.max(140, data.length * 30 + 40)}>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16, top: 4, bottom: 4 }} barCategoryGap={4}>
          <CartesianGrid horizontal={false} stroke="var(--grid)" />
          <XAxis type="number" tick={{ fill: 'var(--muted)', fontSize: 11 }} stroke="var(--grid)" />
          <YAxis type="category" dataKey="name" width={170} tick={{ fill: 'var(--text-2)', fontSize: 11 }} stroke="var(--grid)" />
          <ReferenceLine x={thr} stroke="var(--text-2)" strokeDasharray="4 3" />
          <Tooltip
            cursor={{ fill: 'rgba(255,255,255,0.04)' }}
            contentStyle={{ background: 'var(--panel-2)', border: '1px solid var(--line)', fontSize: 12 }}
            formatter={(v, _n, p) => [`${v} km · Δv ${p.payload.dv} m/s${p.payload.reasons ? ' · ' + p.payload.reasons : ''}`, 'miss']}
          />
          <Bar dataKey="miss" radius={[0, 4, 4, 0]} barSize={14}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.ok ? 'var(--good)' : 'var(--critical)'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <table className="opt-table">
        <thead>
          <tr><th>Option</th><th>Δv m/s</th><th>Miss</th><th>Burn</th><th>Result</th></tr>
        </thead>
        <tbody>
          {comparison.options.map((o) => (
            <tr key={o.maneuver_id} className={comparison.best_option === o.maneuver_id ? 'best' : ''}>
              <td title={o.maneuver_id}>{o.label}</td>
              <td>{o.dv_magnitude_m_s}</td>
              <td>{fmtKm(o.new_miss_km)}</td>
              <td>T-{o.lead_time_before_tca_min.toFixed(0)} min</td>
              <td>{o.meets_criteria ? <span className="tag good">✓ meets</span> : <span className="tag bad" title={o.rejection_reasons.join('\n')}>✕ {o.rejection_reasons[0]}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Approval({ inv, onDone }) {
  const [operator, setOperator] = useState('Flight Director')
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')
  async function decide(decision) {
    setErr('')
    try {
      await api(`/api/investigations/${inv.id}/decision`, { method: 'POST', body: { decision, operator, note } })
      onDone()
    } catch (e) {
      setErr(e.message)
    }
  }
  if (inv.decision) {
    const d = inv.decision
    return (
      <div className={`approval done ${d.decision}`}>
        <strong>{d.decision === 'approve' ? '✓ APPROVED' : '✕ REJECTED'}</strong> by {d.operator}
        {d.note && <> — “{d.note}”</>}
        <p className="muted small">{d.disclaimer}</p>
      </div>
    )
  }
  if (inv.status !== 'awaiting_approval') return null
  return (
    <div className="approval">
      <h4>Human approval required</h4>
      <p className="muted small">Approval is recorded as a hypothetical decision. This system cannot command spacecraft.</p>
      <div className="row">
        <input value={operator} onChange={(e) => setOperator(e.target.value)} placeholder="Operator" maxLength={60} />
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" maxLength={500} />
      </div>
      <div className="row">
        <button className="btn good" onClick={() => decide('approve')}>Approve</button>
        <button className="btn bad" onClick={() => decide('reject')}>Reject</button>
      </div>
      {err && <p className="error">{err}</p>}
    </div>
  )
}

export default function AgentPanel({ inv, onRefresh, onStartRuleBased }) {
  if (!inv) {
    return (
      <div className="empty">
        <p>No active investigation.</p>
        <p className="muted small">Load scenario 2 or 3 — screening auto-triggers the agent — or press “Investigate” on any event.</p>
      </div>
    )
  }
  const rec = inv.recommendation
  const comparison = inv.report?.comparison
  return (
    <div className="agent">
      <div className="inv-head">
        <div>
          <strong>{inv.id}</strong> <span className="muted">· {inv.event_id}</span>
        </div>
        <div className="badges">
          <span className={`tag status-${inv.status}`}>{inv.status.replace('_', ' ')}</span>
          <span className={`tag ${inv.mode.startsWith('llm') ? 'info' : 'warn'}`} title={inv.provider || ''}>
            {inv.mode.startsWith('llm') ? `AI agent · ${inv.provider || '…'}` : inv.mode}
          </span>
        </div>
      </div>
      {inv.error && <p className="error">{inv.error}</p>}
      {inv.status === 'failed' && (
        <button className="btn" onClick={onStartRuleBased}>Retry with rule-based planner</button>
      )}

      {rec && (
        <section className={`rec rec-${rec.decision}`}>
          <h3>
            Recommendation: {rec.decision.replace('_', ' ').toUpperCase()}
            {rec.maneuver && <span className="muted"> · {rec.maneuver.label}</span>}
          </h3>
          <p>{rec.summary}</p>
          {rec.maneuver && (
            <div className="kpis">
              <div><span>Miss</span><b>{fmtKm(rec.maneuver.baseline_miss_km)} → {fmtKm(rec.maneuver.new_miss_km)}</b></div>
              <div><span>Δv</span><b>{rec.maneuver.dv_magnitude_m_s} m/s</b></div>
              <div><span>Propellant</span><b>{rec.maneuver.propellant_kg_est} kg</b></div>
              <div><span>Burn</span><b>{rec.maneuver.burn_time.slice(11, 19)}Z</b></div>
            </div>
          )}
          <ul>{rec.rationale.map((r, i) => <li key={i}>{r}</li>)}</ul>
          <div className="chips">
            Evidence: {rec.evidence_refs.map((r) => <span key={r} className="ref">{r}</span>)}
            <span className={`tag ${rec.grounding_check === 'passed' ? 'good' : 'warn'}`}>numeric grounding {rec.grounding_check}</span>
            <span className="tag">confidence {rec.confidence}</span>
          </div>
          <details>
            <summary>Limitations & next steps</summary>
            <ul>{rec.risks_and_limitations.map((r, i) => <li key={i}>{r}</li>)}</ul>
            <ol>{rec.next_steps.map((r, i) => <li key={i}>{r}</li>)}</ol>
            {inv.report?.uncertainty_and_limitations && (
              <ul className="muted">{inv.report.uncertainty_and_limitations.map((r, i) => <li key={i}>{r}</li>)}</ul>
            )}
          </details>
          <Approval inv={inv} onDone={onRefresh} />
        </section>
      )}

      <ComparisonChart comparison={comparison} />

      <h4>Agent activity <span className="muted small">({inv.steps.filter((s) => s.kind === 'tool').length} tool calls)</span></h4>
      <ol className="steps">
        {inv.steps.map((s) => <Step key={s.id} s={s} />)}
        {inv.status === 'running' && <li className="step running"><span className="spinner" /> agent working…</li>}
      </ol>
    </div>
  )
}
