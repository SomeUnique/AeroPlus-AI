"use client";

import { useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Stars } from "@react-three/drei";
import * as THREE from "three";

import {
  GROUND_STATIONS,
  orbitPosition,
  orbitRingPoints,
  subPointToScene,
  subSatellitePoint,
} from "@/lib/orbit";
import type { AlertLevel } from "@/lib/useAlert";

const ACCENT = new THREE.Color("#46d8ff");
const WARN = new THREE.Color("#ffb547");
const CRIT = new THREE.Color("#ff5b6b");

function alertColor(level: AlertLevel): THREE.Color {
  if (level === "CRITICAL") return CRIT;
  if (level === "WARNING") return WARN;
  return ACCENT;
}

function makeEarthTexture(): THREE.CanvasTexture {
  const w = 1024;
  const h = 512;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d")!;

  ctx.fillStyle = "#08172b";
  ctx.fillRect(0, 0, w, h);
  const ocean = ctx.createLinearGradient(0, 0, 0, h);
  ocean.addColorStop(0, "rgba(20,60,110,0.55)");
  ocean.addColorStop(0.5, "rgba(10,34,66,0.35)");
  ocean.addColorStop(1, "rgba(20,55,105,0.5)");
  ctx.fillStyle = ocean;
  ctx.fillRect(0, 0, w, h);

  // deterministic pseudo-continents
  let seed = 1337;
  const rand = () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  };
  const land = ["#123a33", "#17503f", "#1d5b46", "#123f38"];
  for (let blob = 0; blob < 26; blob += 1) {
    const cx = rand() * w;
    const cy = h * 0.12 + rand() * h * 0.76;
    const parts = 5 + Math.floor(rand() * 6);
    for (let p = 0; p < parts; p += 1) {
      const rx = (0.02 + rand() * 0.06) * w;
      const ry = (0.02 + rand() * 0.05) * h;
      ctx.beginPath();
      ctx.ellipse(
        cx + (rand() - 0.5) * rx * 1.6,
        cy + (rand() - 0.5) * ry * 1.6,
        rx,
        ry,
        0,
        0,
        Math.PI * 2,
      );
      ctx.fillStyle = land[Math.floor(rand() * land.length)];
      ctx.globalAlpha = 0.75;
      ctx.fill();
    }
  }
  ctx.globalAlpha = 1;

  // polar caps
  ctx.fillStyle = "rgba(210,230,245,0.5)";
  ctx.fillRect(0, 0, w, h * 0.04);
  ctx.fillRect(0, h * 0.96, w, h * 0.04);

  // graticule
  ctx.strokeStyle = "rgba(120,190,255,0.10)";
  ctx.lineWidth = 1;
  for (let x = 0; x <= w; x += w / 12) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
  }
  for (let y = 0; y <= h; y += h / 6) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}

const atmosphereVertex = `
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vView = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`;

const atmosphereFragment = `
  uniform vec3 uColor;
  uniform float uPower;
  uniform float uIntensity;
  uniform float uAlert;
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    float rim = 1.0 - max(dot(vNormal, vView), 0.0);
    float intensity = pow(rim, uPower) * uIntensity;
    vec3 color = mix(uColor, vec3(1.0, 0.32, 0.36), uAlert * 0.75);
    gl_FragColor = vec4(color, intensity);
  }
`;

function Globe({ level }: { level: AlertLevel }) {
  const texture = useMemo(() => makeEarthTexture(), []);
  const groupRef = useRef<THREE.Group>(null);

  useFrame((_, dt) => {
    if (groupRef.current) groupRef.current.rotation.y += dt * 0.02;
  });

  const atmosphereUniforms = useMemo(
    () => ({
      uColor: { value: ACCENT.clone() },
      uPower: { value: 3.2 },
      uIntensity: { value: 0.9 },
      uAlert: { value: 0 },
    }),
    [],
  );

  useFrame((_, dt) => {
    const target = level === "NOMINAL" ? 0 : level === "WARNING" ? 0.6 : 1;
    atmosphereUniforms.uAlert.value +=
      (target - atmosphereUniforms.uAlert.value) * Math.min(1, dt * 3);
  });

  return (
    <group ref={groupRef}>
      <mesh>
        <sphereGeometry args={[1, 64, 48]} />
        <meshStandardMaterial map={texture} roughness={0.92} metalness={0.05} />
      </mesh>
      <mesh scale={1.14}>
        <sphereGeometry args={[1, 48, 32]} />
        <shaderMaterial
          vertexShader={atmosphereVertex}
          fragmentShader={atmosphereFragment}
          uniforms={atmosphereUniforms}
          transparent
          side={THREE.BackSide}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>
    </group>
  );
}

function OrbitPath() {
  const line = useMemo(() => {
    const points = orbitRingPoints(256).map((p) => new THREE.Vector3(...p));
    const geom = new THREE.BufferGeometry().setFromPoints(points);
    const mat = new THREE.LineBasicMaterial({
      color: ACCENT,
      transparent: true,
      opacity: 0.22,
    });
    return new THREE.Line(geom, mat);
  }, []);
  return <primitive object={line} />;
}

function GroundTrack() {
  const COUNT = 200;
  const line = useMemo(() => {
    const geom = new THREE.BufferGeometry();
    geom.setAttribute(
      "position",
      new THREE.BufferAttribute(new Float32Array((COUNT - 1) * 2 * 3), 3),
    );
    const mat = new THREE.LineBasicMaterial({
      color: ACCENT,
      transparent: true,
      opacity: 0.6,
    });
    return new THREE.LineSegments(geom, mat);
  }, []);

  useFrame(() => {
    const t = Date.now() / 1000;
    const pos = line.geometry.attributes.position as THREE.BufferAttribute;
    const arr = pos.array as Float32Array;
    const span = 5400;
    let prev = subSatellitePoint(t - span / 2);
    for (let i = 1; i < COUNT; i += 1) {
      const cur = subSatellitePoint(t - span / 2 + (i / (COUNT - 1)) * span);
      const a = subPointToScene(prev, 1.004);
      const b = subPointToScene(cur, 1.004);
      const idx = (i - 1) * 6;
      const jump = Math.abs(cur.lon - prev.lon) > 180;
      if (jump) {
        arr[idx] = a[0]; arr[idx + 1] = a[1]; arr[idx + 2] = a[2];
        arr[idx + 3] = a[0]; arr[idx + 4] = a[1]; arr[idx + 5] = a[2];
      } else {
        arr[idx] = a[0]; arr[idx + 1] = a[1]; arr[idx + 2] = a[2];
        arr[idx + 3] = b[0]; arr[idx + 4] = b[1]; arr[idx + 5] = b[2];
      }
      prev = cur;
    }
    pos.needsUpdate = true;
  });

  return <primitive object={line} />;
}

function Satellite({ level }: { level: AlertLevel }) {
  const group = useRef<THREE.Group>(null);
  const core = useRef<THREE.Mesh>(null);
  const material = useMemo(
    () =>
      new THREE.MeshBasicMaterial({
        color: ACCENT.clone(),
        transparent: true,
        opacity: 0.95,
      }),
    [],
  );

  useFrame((state) => {
    const t = Date.now() / 1000;
    const [x, y, z] = orbitPosition(t);
    if (group.current) group.current.position.set(x, y, z);
    if (core.current) {
      const pulse = 0.85 + Math.sin(state.clock.elapsedTime * 4) * 0.15;
      const scale = level === "NOMINAL" ? 1 : 1.25 * pulse;
      core.current.scale.setScalar(scale);
    }
    const target = alertColor(level);
    material.color.lerp(target, 0.1);
  });

  return (
    <group ref={group}>
      <mesh ref={core}>
        <octahedronGeometry args={[0.035, 0]} />
        <primitive object={material} attach="material" />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.075, 16, 16]} />
        <meshBasicMaterial
          color={ACCENT}
          transparent
          opacity={0.18}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>
    </group>
  );
}

function GroundStations() {
  const markers = useMemo(
    () =>
      GROUND_STATIONS.map((s) => ({
        pos: new THREE.Vector3(...subPointToScene(s, 1.006)),
        color: ACCENT,
      })),
    [],
  );
  return (
    <>
      {markers.map((m, i) => (
        <mesh key={i} position={m.pos}>
          <sphereGeometry args={[0.012, 10, 10]} />
          <meshBasicMaterial color={m.color} />
        </mesh>
      ))}
    </>
  );
}

function PerfProbe({ onFps }: { onFps?: (fps: number) => void }) {
  const acc = useRef({ frames: 0, t: 0 });
  useFrame((_, dt) => {
    if (!onFps) return;
    const a = acc.current;
    a.frames += 1;
    a.t += dt;
    if (a.t >= 1) {
      onFps(a.frames / a.t);
      a.frames = 0;
      a.t = 0;
    }
  });
  return null;
}

export default function Earth3D({
  level = "NOMINAL",
  onFps,
}: {
  level: AlertLevel;
  onFps?: (fps: number) => void;
}) {
  return (
    <Canvas
      dpr={[1, 1.75]}
      camera={{ position: [1.2, 1.1, 3.1], fov: 42, near: 0.1, far: 100 }}
      gl={{ antialias: true, powerPreference: "high-performance" }}
    >
      <color attach="background" args={["#04060c"]} />
      <ambientLight intensity={0.35} />
      <directionalLight position={[5, 2, 4]} intensity={2.1} />
      <Stars radius={60} depth={30} count={1400} factor={3} fade speed={0.4} />
      <Globe level={level} />
      <OrbitPath />
      <GroundTrack />
      <GroundStations />
      <Satellite level={level} />
      <PerfProbe onFps={onFps} />
    </Canvas>
  );
}
