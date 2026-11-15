"use client";

import { Line, OrbitControls, Stars } from "@react-three/drei";
import { Canvas, useFrame, useLoader } from "@react-three/fiber";
import { Suspense, useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { clock, OMEGA_EARTH, positionAt, R_EARTH_KM } from "@/lib/clock";
import type { CloseEvent, SatObject, Trajectories } from "@/lib/types";

const S = 1 / 1000; // 1 scene unit = 1000 km
const tmp: [number, number, number] = [0, 0, 0];

/** TEME (z = north) -> three.js (y = up). */
function toScene(p: [number, number, number], v: THREE.Vector3) {
  return v.set(p[0] * S, p[2] * S, -p[1] * S);
}

const KIND_COLOR: Record<SatObject["kind"], string> = {
  PAYLOAD: "#7cc4ff",
  DEBRIS: "#9aa6b8",
  ROCKET_BODY: "#d2b98a",
};
const SEV_COLOR = { RED: "#f87171", AMBER: "#fbbf24", WATCH: "#93c5fd" } as const;

interface Props {
  traj: Trajectories | null;
  objects: SatObject[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  event: CloseEvent | null;
}

function ClockDriver() {
  useFrame((_, delta) => {
    if (clock.playing) {
      clock.t += Math.min(delta, 0.1) * clock.speed;
      if (clock.t > clock.horizon) clock.t = 0;
    }
  });
  return null;
}

function Earth({ gmst0 }: { gmst0: number }) {
  const ref = useRef<THREE.Group>(null);
  const [day] = useLoader(THREE.TextureLoader, ["/textures/earth.jpg"]);
  day.colorSpace = THREE.SRGBColorSpace;
  day.anisotropy = 8;
  useFrame(() => {
    if (ref.current) ref.current.rotation.y = gmst0 + OMEGA_EARTH * clock.t;
  });
  const r = R_EARTH_KM * S;
  return (
    <group ref={ref}>
      <mesh>
        <sphereGeometry args={[r, 96, 96]} />
        <meshPhongMaterial map={day} shininess={6} specular={new THREE.Color("#1c3550")} />
      </mesh>
      <mesh scale={1.025}>
        <sphereGeometry args={[r, 64, 64]} />
        <meshBasicMaterial color="#4aa3ff" transparent opacity={0.08} side={THREE.BackSide} />
      </mesh>
    </group>
  );
}

function FallbackEarth() {
  return (
    <mesh>
      <sphereGeometry args={[R_EARTH_KM * S, 48, 48]} />
      <meshPhongMaterial color="#123a63" wireframe />
    </mesh>
  );
}

function OrbitPath({ traj, obj, color, width, opacity }: { traj: Trajectories; obj: SatObject; color: string; width: number; opacity: number }) {
  const points = useMemo(() => {
    const period = obj.elements.period_min * 60;
    const n = Math.min(traj.n, Math.ceil(period / traj.step_s) + 2);
    const pts: THREE.Vector3[] = [];
    for (let i = 0; i < n; i++) {
      if (positionAt(traj, obj.id, i * traj.step_s, tmp)) pts.push(toScene(tmp, new THREE.Vector3()));
    }
    return pts;
  }, [traj, obj]);
  if (points.length < 2) return null;
  return <Line points={points} color={color} lineWidth={width} transparent opacity={opacity} />;
}

function Satellite({ traj, obj, color, size, selected, label, onSelect }: {
  traj: Trajectories; obj: SatObject; color: string; size: number; selected: boolean; label: boolean; onSelect: (id: string) => void;
}) {
  const ref = useRef<THREE.Group>(null);
  useFrame(() => {
    if (!ref.current) return;
    const ok = positionAt(traj, obj.id, clock.t, tmp);
    ref.current.visible = ok;
    if (ok) toScene(tmp, ref.current.position);
  });
  return (
    <group ref={ref}>
      <mesh
        onClick={(e) => {
          e.stopPropagation();
          onSelect(obj.id);
        }}
        onPointerOver={() => (document.body.style.cursor = "pointer")}
        onPointerOut={() => (document.body.style.cursor = "")}
      >
        <sphereGeometry args={[size * 2.5, 8, 8]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      <mesh>
        <sphereGeometry args={[size, 12, 12]} />
        <meshBasicMaterial color={color} />
      </mesh>
      {selected && (
        <mesh>
          <ringGeometry args={[size * 2.2, size * 2.8, 32]} />
          <meshBasicMaterial color="#ffffff" side={THREE.DoubleSide} />
        </mesh>
      )}
      {label && <Label text={obj.name} y={size * 5} />}
    </group>
  );
}

/** Canvas-texture sprite label (avoids per-label React roots that drei <Html> creates). */
function Label({ text, y }: { text: string; y: number }) {
  const { texture, aspect } = useMemo(() => {
    const font = "500 30px Jost, ui-sans-serif, system-ui, sans-serif";
    const c = document.createElement("canvas");
    const ctx = c.getContext("2d")!;
    ctx.font = font;
    const w = Math.ceil(ctx.measureText(text).width) + 28;
    c.width = w;
    c.height = 48;
    ctx.font = font;
    ctx.fillStyle = "rgba(13,30,51,0.88)";
    ctx.strokeStyle = "#2c5482";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(1, 1, w - 2, 46, 10);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#f2f6fb";
    ctx.textBaseline = "middle";
    ctx.fillText(text, 14, 25);
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    return { texture: tex, aspect: w / 48 };
  }, [text]);
  useEffect(() => () => texture.dispose(), [texture]);
  const h = 0.32;
  return (
    <sprite position={[0, y + h / 2, 0]} scale={[h * aspect, h, 1]} renderOrder={10}>
      <spriteMaterial map={texture} depthTest={false} transparent />
    </sprite>
  );
}

/** Pulsing marker at the predicted encounter point + live link line between the pair. */
function Encounter({ traj, event }: { traj: Trajectories; event: CloseEvent }) {
  const marker = useRef<THREE.Mesh>(null);
  const lineGeom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
    return g;
  }, []);
  const at = useMemo(() => {
    const p: [number, number, number] = [0, 0, 0];
    return positionAt(traj, event.primary_id, event.tca_s, p) ? toScene(p, new THREE.Vector3()) : null;
  }, [traj, event]);
  const color = SEV_COLOR[event.severity];
  const lineObj = useMemo(() => new THREE.Line(lineGeom, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.9 })), [lineGeom, color]);

  useFrame(({ clock: c }) => {
    if (marker.current) {
      const s = 1 + 0.5 * Math.sin(c.elapsedTime * 4);
      marker.current.scale.setScalar(s);
      marker.current.lookAt(0, 0, 0);
    }
    const a: [number, number, number] = [0, 0, 0];
    const b: [number, number, number] = [0, 0, 0];
    const near = Math.abs(clock.t - event.tca_s) < 1200;
    const ok = near && positionAt(traj, event.primary_id, clock.t, a) && positionAt(traj, event.secondary_id, clock.t, b);
    lineObj.visible = !!ok;
    if (ok) {
      const pos = lineGeom.getAttribute("position") as THREE.BufferAttribute;
      const va = toScene(a, new THREE.Vector3());
      const vb = toScene(b, new THREE.Vector3());
      pos.setXYZ(0, va.x, va.y, va.z);
      pos.setXYZ(1, vb.x, vb.y, vb.z);
      pos.needsUpdate = true;
    }
  });
  return (
    <>
      {at && (
        <mesh ref={marker} position={at}>
          <ringGeometry args={[0.1, 0.14, 40]} />
          <meshBasicMaterial color={color} side={THREE.DoubleSide} transparent opacity={0.9} />
        </mesh>
      )}
      <primitive object={lineObj} />
    </>
  );
}

export default function Globe({ traj, objects, selectedId, onSelect, event }: Props) {
  const many = objects.length > 40;
  const involved = new Set(event ? [event.primary_id, event.secondary_id] : []);
  return (
    <Canvas camera={{ position: [0, 8, 20], fov: 45, near: 0.01, far: 2000 }} dpr={[1, 1.75]} gl={{ antialias: true }} fallback={<div className="mission-globe-fallback" role="status">3D view requires WebGL</div>}>
      <color attach="background" args={["#03070e"]} />
      {traj && <ClockDriver />}
      <ambientLight intensity={0.55} />
      <directionalLight position={[30, 12, 20]} intensity={2.1} />
      <Stars radius={300} depth={60} count={4000} factor={6} fade speed={0.3} />
      <Suspense fallback={<FallbackEarth />}>
        <Earth gmst0={traj?.gmst0_rad ?? 0} />
      </Suspense>
      {traj && objects.map((o) => {
        const isEvent = involved.has(o.id);
        const isSel = o.id === selectedId;
        const color = isEvent && event ? SEV_COLOR[event.severity] : KIND_COLOR[o.kind];
        const showPath = !many || isEvent || isSel;
        return (
          <group key={o.id}>
            {showPath && (
              <OrbitPath
                traj={traj}
                obj={o}
                color={isSel ? "#ffffff" : color}
                width={isEvent || isSel ? 1.8 : 0.8}
                opacity={isEvent || isSel ? 0.85 : 0.28}
              />
            )}
            <Satellite
              traj={traj}
              obj={o}
              color={isSel ? "#ffffff" : color}
              size={isEvent ? 0.075 : many ? 0.04 : 0.055}
              selected={isSel}
              label={isEvent || isSel}
              onSelect={onSelect}
            />
          </group>
        );
      })}
      {traj && event && <Encounter traj={traj} event={event} />}
      <OrbitControls enablePan={false} minDistance={8} maxDistance={70} rotateSpeed={0.5} zoomSpeed={0.7} />
    </Canvas>
  );
}
