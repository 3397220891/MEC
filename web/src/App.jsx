import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, fmtKm, fmtTime, levelClass } from './api'
import Globe, { colorFor, positionAt } from './components/Globe'
import AgentPanel from './components/AgentPanel'

const SPEEDS = [1, 10, 60, 300]

function SourceBadge({ status }) {
  if (!status?.loaded) return null
  const label = status.simulated
    ? 'SIMULATED DATA'
    : status.data_source === 'celestrak-live'
      ? 'LIVE · CELESTRAK'
      : `SNAPSHOT · ${fmtTime(status.fetched_utc)}`
  return <span className={`tag ${status.simulated ? 'warn' : 'info'}`} title={status.data_source_detail}>{label}</span>
}

function ObjectList({ objects, events, selectedId, onSelect }) {
  const [q, setQ] = useState('')
  const flagged = new Set(events.filter((e) => e.level !== 'WATCH').flatMap((e) => [e.primary_id, e.secondary_id]))
  const shown = objects.filter((o) => (o.name + o.id).toLowerCase().includes(q.toLowerCase()))
  return (
    <>
      <input className="search" placeholder={`Search ${objects.length} objects…`} value={q} onChange={(e) => setQ(e.target.value)} />
      <ul className="objlist">
        {shown.map((o) => (
          <li key={o.id} className={o.id === selectedId ? 'sel' : ''} onClick={() => onSelect(o.id)}>
            <span className="dot" style={{ background: colorFor(o, flagged.has(o.id)) }} />
            <span className="oname">{o.name}</span>
            {flagged.has(o.id) && <span className="tag bad tiny">!</span>}
            <span className="muted small">{o.state ? `${o.state.altitude_km.toFixed(0)} km` : '—'}</span>
          </li>
        ))}
      </ul>
    </>
  )
}

function ObjectDetail({ obj, traj, simS }) {
  if (!obj) return <p className="muted small">Select an object in the list or on the globe.</p>
  const p = positionAt(traj, obj.id, simS)
  const p2 = positionAt(traj, obj.id, simS + 1)
  const alt = p ? Math.hypot(...p) - 6378.137 : null
  const speed = p && p2 ? Math.hypot(p2[0] - p[0], p2[1] - p[1], p2[2] - p[2]) : null
  const el = obj.elements
  return (
    <div className="detail">
      <h3>{obj.name}</h3>
      <div className="chips">
        <span className="tag">{obj.kind}</span>
        {obj.simulated && <span className="tag warn">SIM</span>}
        {obj.maneuverable && <span className="tag info">maneuverable</span>}
      </div>
      <dl>
        <dt>ID</dt><dd>{obj.id}</dd>
        <dt>Operator</dt><dd>{obj.operator || '—'}</dd>
        <dt>Altitude (geocentric)</dt><dd>{alt != null ? `${alt.toFixed(1)} km` : '—'}</dd>
        <dt>Speed</dt><dd>{speed != null ? `${speed.toFixed(3)} km/s` : '—'}</dd>
        <dt>Perigee / apogee</dt><dd>{el.perigee_alt_km} / {el.apogee_alt_km} km</dd>
        <dt>Inclination</dt><dd>{el.inclination_deg}°</dd>
        <dt>RAAN</dt><dd>{el.raan_deg}°</dd>
        <dt>Eccentricity</dt><dd>{el.eccentricity}</dd>
        <dt>Period</dt><dd>{el.period_min} min</dd>
        <dt>Element epoch</dt><dd>{fmtTime(obj.tle_epoch)}</dd>
        <dt>Freshness</dt><dd>{obj.freshness}</dd>
      </dl>
    </div>
  )
}

function Timeline({ status, simS, onSeek }) {
  const h = status.horizon_s
  return (
    <div className="timeline">
      <input type="range" min={0} max={h} step={10} value={simS} onChange={(e) => onSeek(Number(e.target.value))} />
      <div className="ticks">
        {status.events.map((e) => (
          <button
            key={e.id}
            className={`tick ${levelClass(e.level)}`}
            style={{ left: `${(e.tca_offset_s / h) * 100}%` }}
            title={`${e.id} ${e.level} ${fmtKm(e.miss_km)} @ ${fmtTime(e.tca)}`}
            onClick={() => onSeek(Math.max(0, e.tca_offset_s - 60))}
          />
        ))}
      </div>
    </div>
  )
}

export default function App() {
  const [scenarios, setScenarios] = useState([])
  const [status, setStatus] = useState(null)
  const [objects, setObjects] = useState([])
  const [traj, setTraj] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [invId, setInvId] = useState(null)
  const [inv, setInv] = useState(null)
  const [logs, setLogs] = useState([])
  const [health, setHealth] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState(60)
  const [simS, setSimS] = useState(0)
  const simRef = useRef(0)
  const playingRef = useRef(true)
  const speedRef = useRef(60)
  playingRef.current = playing
  speedRef.current = speed

  useEffect(() => {
    api('/api/scenarios').then(setScenarios).catch((e) => setError(`Backend not reachable: ${e.message}`))
    api('/api/health').then(setHealth).catch(() => {})
    api('/api/status').then((s) => s.loaded && hydrate(s)).catch(() => {})
  }, [])

  // HUD clock: the 3D scene advances simRef every frame; mirror it into React state 4x per second.
  useEffect(() => {
    const t = setInterval(() => setSimS(simRef.current), 250)
    return () => clearInterval(t)
  }, [])

  const hydrate = useCallback(async (s) => {
    setStatus(s)
    const [objs, tr, invs] = await Promise.all([
      api('/api/objects'),
      api(`/api/trajectories?duration_min=${Math.round(s.horizon_s / 60)}&step_s=30`),
      api('/api/investigations'),
    ])
    setObjects(objs)
    setTraj(tr)
    if (invs.length) setInvId(invs[0].id)
  }, [])

  async function loadScenario(id) {
    setBusy(true)
    setError('')
    setInv(null)
    setInvId(null)
    setSelectedId(null)
    try {
      const s = await api(`/api/scenarios/${id}/load`, { method: 'POST' })
      simRef.current = 0
      await hydrate(s)
      if (s.auto_investigation_id) setInvId(s.auto_investigation_id)
      const ev = s.events.find((e) => e.level === 'RED') || s.events[0]
      if (ev) setSelectedId(ev.primary_id)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // Poll the investigation while it is running (agent steps stream in as they are logged).
  const refreshInv = useCallback(async () => {
    if (!invId) return
    try {
      setInv(await api(`/api/investigations/${invId}`))
    } catch (e) {
      setError(e.message)
    }
  }, [invId])
  const invStatusRef = useRef(null)
  invStatusRef.current = inv?.status
  useEffect(() => {
    refreshInv()
    if (!invId) return
    const t = setInterval(() => {
      if (!invStatusRef.current || invStatusRef.current === 'running') refreshInv()
    }, 1000)
    return () => clearInterval(t)
  }, [invId, refreshInv])

  useEffect(() => {
    if (!status?.loaded) return
    const load = () => api('/api/logs?limit=80').then(setLogs).catch(() => {})
    load()
    const t = setInterval(load, 2500)
    return () => clearInterval(t)
  }, [status?.scenario])

  async function investigate(eventId, ruleBased = false) {
    try {
      const r = await api('/api/investigations', { method: 'POST', body: { event_id: eventId, rule_based: ruleBased } })
      setInv(null)
      setInvId(r.investigation_id)
    } catch (e) {
      setError(e.message)
    }
  }

  const seek = (s) => {
    simRef.current = s
    setSimS(s)
  }
  const selected = objects.find((o) => o.id === selectedId)
  const nameOf = (id) => objects.find((o) => o.id === id)?.name || id
  const t0ms = status?.scenario_start ? Date.parse(status.scenario_start) : Date.now()
  const simIso = new Date(t0ms + simS * 1000).toISOString().slice(0, 19) + 'Z'
  const events = status?.events || []
  const nextEvent = useMemo(() => events.filter((e) => e.tca_offset_s > simS).sort((a, b) => a.tca_offset_s - b.tca_offset_s)[0], [events, simS])

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="logo">◎</span> OrbitalGuard <span className="ai">AI</span>
          <span className="muted small"> · collision risk decision support</span>
        </div>
        <nav className="scenarios">
          {scenarios.map((s) => (
            <button key={s.id} className={status?.scenario === s.id ? 'active' : ''} onClick={() => loadScenario(s.id)} disabled={busy} title={s.description}>
              {s.name}
            </button>
          ))}
        </nav>
        <div className="right-badges">
          <SourceBadge status={status} />
          {health && (
            <span className={`tag ${health.providers.length ? 'info' : 'warn'}`} title={health.agent_mode}>
              LLM: {health.providers.map((p) => p.name).join(' → ') || 'offline (rule-based)'}
            </span>
          )}
        </div>
      </header>

      {error && <div className="banner error" onClick={() => setError('')}>{error} (click to dismiss)</div>}

      <aside className="left panel">
        <h2>Tracked objects</h2>
        {status?.loaded ? (
          <>
            <ObjectList objects={objects} events={events} selectedId={selectedId} onSelect={setSelectedId} />
            <ObjectDetail obj={selected} traj={traj} simS={simS} />
          </>
        ) : (
          <p className="muted">Choose a scenario above to begin.</p>
        )}
      </aside>

      <main className="center">
        <Globe traj={traj} objects={objects} events={events} selectedId={selectedId} onSelect={setSelectedId}
          simRef={simRef} playingRef={playingRef} speedRef={speedRef} t0ms={t0ms} />
        {status?.loaded && (
          <div className="hud">
            <div className="clock">
              <span className="muted small">SIM TIME</span>
              <b>{simIso.replace('T', ' ')}</b>
              <span className="muted small">T+{(simS / 3600).toFixed(2)} h</span>
            </div>
            <div className="controls">
              <button onClick={() => setPlaying(!playing)}>{playing ? '❚❚' : '▶'}</button>
              {SPEEDS.map((v) => (
                <button key={v} className={speed === v ? 'active' : ''} onClick={() => setSpeed(v)}>{v}×</button>
              ))}
              {nextEvent && <button onClick={() => seek(Math.max(0, nextEvent.tca_offset_s - 120))}>⏭ next TCA</button>}
            </div>
          </div>
        )}
        {status?.loaded && <div className="disclaimer">Screening tool for decision support · not flight software · no spacecraft command capability</div>}
      </main>

      <section className="bottom panel">
        {status?.loaded ? (
          <>
            <Timeline status={status} simS={simS} onSeek={seek} />
            <div className="bottom-grid">
              <div>
                <h2>
                  Close-approach alerts{' '}
                  <span className="muted small">
                    {status.object_count} objects · {status.horizon_s / 3600} h horizon · {status.threshold_km} km threshold · screened in {status.screen_ms} ms
                  </span>
                </h2>
                {events.length === 0 ? (
                  <p className="good-text">✓ No close approaches within {status.threshold_km} km over the screening horizon.</p>
                ) : (
                  <table className="events">
                    <thead>
                      <tr><th>Level</th><th>Objects</th><th>TCA (UTC)</th><th>Miss</th><th>R / T / N km</th><th>v rel</th><th /></tr>
                    </thead>
                    <tbody>
                      {events.map((e) => (
                        <tr key={e.id}>
                          <td><span className={`tag ${levelClass(e.level)}`}>{e.level}</span></td>
                          <td>{nameOf(e.primary_id)} × {nameOf(e.secondary_id)}</td>
                          <td>{fmtTime(e.tca)}</td>
                          <td><b>{fmtKm(e.miss_km)}</b></td>
                          <td className="small">{e.radial_km.toFixed(3)} / {e.intrack_km.toFixed(3)} / {e.crosstrack_km.toFixed(3)}</td>
                          <td>{e.rel_speed_km_s.toFixed(2)} km/s</td>
                          <td className="nowrap">
                            <button className="btn small" onClick={() => seek(Math.max(0, e.tca_offset_s - 120))}>View</button>
                            <button className="btn small primary" onClick={() => investigate(e.id)}>Investigate</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              <div>
                <h2>Mission log</h2>
                <ul className="log">
                  {logs.map((l) => (
                    <li key={l.id} className={`log-${l.level}`}>
                      <span className="muted small">{new Date(l.ts * 1000).toLocaleTimeString()}</span>{' '}
                      <span className="cat">{l.category}</span> {l.message}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </>
        ) : (
          <p className="muted">Mission timeline and alerts appear here.</p>
        )}
      </section>

      <aside className="right panel">
        <h2>Collision investigation agent</h2>
        <AgentPanel inv={inv} onRefresh={refreshInv} onStartRuleBased={() => inv && investigate(inv.event_id, true)} />
      </aside>
    </div>
  )
}
