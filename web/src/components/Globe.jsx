import { useMemo, useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Html, Line, OrbitControls, Stars } from '@react-three/drei'
import * as THREE from 'three'

// Units: 1 scene unit = 1000 km. TEME (x, y, z) -> scene (x, z, -y) so Earth's north pole is +Y.
const S = 1 / 1000
const RE = 6.378137
const toScene = (x, y, z) => [x * S, z * S, -y * S]

// Greenwich mean sidereal time (IAU-82), same formula as the backend.
export function gmst(ms) {
  const jd = ms / 86400000 + 2440587.5
  const t = (jd - 2451545.0) / 36525
  const sec = 67310.54841 + (876600 * 3600 + 8640184.812866) * t + 0.093104 * t * t - 6.2e-6 * t ** 3
  return ((((sec / 240) % 360) + 360) % 360) * (Math.PI / 180)
}

// Linear interpolation inside the bulk trajectory arrays from /api/trajectories.
export function positionAt(traj, id, offsetS) {
  const arr = traj?.objects?.[id]
  if (!arr) return null
  const n = traj.count
  const f = Math.min(Math.max(offsetS / traj.step_s, 0), n - 1)
  const k = Math.min(Math.floor(f), n - 2)
  const u = f - k
  const a = k * 3
  const b = a + 3
  return [0, 1, 2].map((i) => arr[a + i] + (arr[b + i] - arr[a + i]) * u)
}

const COLORS = { fleet: '#4cc9f0', payload: '#8fa3bf', debris: '#f4a261', rocket_body: '#c3a6ff', flagged: '#ff4d6d' }
export const colorFor = (o, flagged) =>
  flagged ? COLORS.flagged : o.maneuverable && o.simulated ? COLORS.fleet : COLORS[o.kind] || COLORS.payload

function Earth({ simRef, t0ms }) {
  const ref = useRef()
  const graticule = useMemo(() => {
    const lines = []
    const r = RE * 1.001
    for (let lat = -60; lat <= 60; lat += 30) {
      const pts = []
      for (let lon = 0; lon <= 360; lon += 5) {
        const la = (lat * Math.PI) / 180
        const lo = (lon * Math.PI) / 180
        pts.push(toScene(r * 1000 * Math.cos(la) * Math.cos(lo), r * 1000 * Math.cos(la) * Math.sin(lo), r * 1000 * Math.sin(la)))
      }
      lines.push({ pts, equator: lat === 0 })
    }
    for (let lon = 0; lon < 360; lon += 30) {
      const pts = []
      for (let lat = -90; lat <= 90; lat += 5) {
        const la = (lat * Math.PI) / 180
        const lo = (lon * Math.PI) / 180
        pts.push(toScene(r * 1000 * Math.cos(la) * Math.cos(lo), r * 1000 * Math.cos(la) * Math.sin(lo), r * 1000 * Math.sin(la)))
      }
      lines.push({ pts, prime: lon === 0 })
    }
    return lines
  }, [])
  useFrame(() => {
    if (ref.current) ref.current.rotation.y = gmst(t0ms + simRef.current * 1000)
  })
  return (
    <group ref={ref}>
      <mesh>
        <sphereGeometry args={[RE, 96, 64]} />
        <meshPhongMaterial color="#0b2340" emissive="#06121f" shininess={8} />
      </mesh>
      {graticule.map((g, i) => (
        <Line key={i} points={g.pts} color={g.equator || g.prime ? '#3a86ff' : '#1d4e89'} lineWidth={g.equator || g.prime ? 1.2 : 0.6} transparent opacity={0.7} />
      ))}
      <mesh scale={1.025}>
        <sphereGeometry args={[RE, 64, 48]} />
        <meshBasicMaterial color="#3a86ff" transparent opacity={0.08} side={THREE.BackSide} />
      </mesh>
    </group>
  )
}

function Satellites({ traj, objects, flagged, selectedId, onSelect, simRef }) {
  const refs = useRef({})
  const labelRef = useRef()
  useFrame(() => {
    for (const o of objects) {
      const m = refs.current[o.id]
      const p = positionAt(traj, o.id, simRef.current)
      if (m && p) m.position.set(...toScene(...p))
      if (o.id === selectedId && labelRef.current && p) labelRef.current.position.set(...toScene(...p))
    }
  })
  return (
    <>
      {objects.map((o) => {
        const isFlag = flagged.has(o.id)
        const sel = o.id === selectedId
        return (
          <mesh
            key={o.id}
            ref={(m) => (refs.current[o.id] = m)}
            onClick={(e) => {
              e.stopPropagation()
              onSelect(o.id)
            }}
            scale={sel ? 2.2 : isFlag ? 1.6 : 1}
          >
            <sphereGeometry args={[0.055, 12, 12]} />
            <meshBasicMaterial color={sel ? '#ffffff' : colorFor(o, isFlag)} />
          </mesh>
        )
      })}
      {selectedId && (
        <group ref={labelRef}>
          <Html distanceFactor={14} style={{ pointerEvents: 'none' }}>
            <div className="globe-label">{objects.find((o) => o.id === selectedId)?.name}</div>
          </Html>
        </group>
      )}
    </>
  )
}

function Orbits({ traj, objects, flagged, selectedId }) {
  const lines = useMemo(() => {
    if (!traj) return []
    return objects.map((o) => {
      const arr = traj.objects[o.id]
      const pts = []
      // one orbit-ish of path (~100 min) keeps the scene readable; flagged/selected get the full horizon
      const full = flagged.has(o.id) || o.id === selectedId
      const n = full ? traj.count : Math.min(traj.count, Math.ceil(6000 / traj.step_s))
      for (let k = 0; k < n; k++) pts.push(toScene(arr[k * 3], arr[k * 3 + 1], arr[k * 3 + 2]))
      return { id: o.id, pts, color: colorFor(o, flagged.has(o.id)), strong: full }
    })
  }, [traj, objects, flagged, selectedId])
  return lines.map((l) => (
    <Line key={l.id} points={l.pts} color={l.color} lineWidth={l.strong ? 1.6 : 0.7} transparent opacity={l.strong ? 0.85 : 0.3} />
  ))
}

function EventMarkers({ traj, events, simRef }) {
  const rings = useRef([])
  useFrame(({ clock }) => {
    const s = 1 + 0.35 * Math.sin(clock.elapsedTime * 4)
    rings.current.forEach((r) => r && r.scale.setScalar(s))
  })
  const shown = events.filter((e) => e.level === 'RED' || e.level === 'AMBER')
  return shown.map((e, i) => {
    const p = positionAt(traj, e.primary_id, e.tca_offset_s)
    if (!p) return null
    const pos = toScene(...p)
    const color = e.level === 'RED' ? '#ff4d6d' : '#ffb703'
    return (
      <group key={e.id} position={pos}>
        <mesh ref={(m) => (rings.current[i] = m)}>
          <sphereGeometry args={[0.16, 16, 16]} />
          <meshBasicMaterial color={color} transparent opacity={0.35} />
        </mesh>
        <Html distanceFactor={16} style={{ pointerEvents: 'none' }}>
          <div className={`globe-event ${e.level === 'RED' ? 'red' : 'amber'}`}>
            {e.level} · {(e.miss_km * 1000).toFixed(0)} m
          </div>
        </Html>
      </group>
    )
  })
}

function Clock({ simRef, playingRef, speedRef, maxS }) {
  useFrame((_, dt) => {
    if (playingRef.current) simRef.current = Math.min(simRef.current + dt * speedRef.current, maxS)
  })
  return null
}

export default function Globe({ traj, objects, events, selectedId, onSelect, simRef, playingRef, speedRef, t0ms }) {
  const flagged = useMemo(() => {
    const s = new Set()
    events.filter((e) => e.level !== 'WATCH').forEach((e) => (s.add(e.primary_id), s.add(e.secondary_id)))
    return s
  }, [events])
  const maxS = traj ? (traj.count - 1) * traj.step_s : 0
  return (
    <Canvas camera={{ position: [14, 9, 18], fov: 45, near: 0.05, far: 2000 }} onPointerMissed={() => onSelect(null)}>
      <color attach="background" args={['#02060d']} />
      <ambientLight intensity={0.5} />
      <directionalLight position={[30, 10, 20]} intensity={1.4} />
      <Stars radius={300} depth={60} count={4000} factor={6} fade />
      <Clock simRef={simRef} playingRef={playingRef} speedRef={speedRef} maxS={maxS} />
      <Earth simRef={simRef} t0ms={t0ms} />
      {traj && (
        <>
          <Orbits traj={traj} objects={objects} flagged={flagged} selectedId={selectedId} />
          <Satellites traj={traj} objects={objects} flagged={flagged} selectedId={selectedId} onSelect={onSelect} simRef={simRef} />
          <EventMarkers traj={traj} events={events} simRef={simRef} />
        </>
      )}
      <OrbitControls enablePan={false} minDistance={8} maxDistance={80} />
    </Canvas>
  )
}
