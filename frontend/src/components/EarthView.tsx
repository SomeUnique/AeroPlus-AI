"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Satellite } from "lucide-react";

import { MISSION } from "@/lib/config";
import {
  EARTH_RADIUS_KM,
  GROUND_STATIONS,
  MU_KM3_S2,
  ORBIT_PERIOD_S,
  formatDuration,
  nextPass,
  type PassWindow,
} from "@/lib/orbit";
import { useTelemetryStore } from "@/lib/store";
import type { AlertLevel } from "@/lib/useAlert";
import { Label } from "./ui/Label";
import { Panel } from "./ui/Panel";
import { StatusBadge } from "./ui/StatusBadge";
import { OrbitMap2D } from "./earth/OrbitMap2D";

const Earth3D = dynamic(() => import("./earth/Earth3D"), { ssr: false });

type Mode = "pending" | "3d" | "2d";

function supportsWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(
      canvas.getContext("webgl2") ?? canvas.getContext("webgl"),
    );
  } catch {
    return false;
  }
}

const ORBITAL_VELOCITY =
  Math.sqrt(MU_KM3_S2 / (EARTH_RADIUS_KM + MISSION.altitudeKm)) / 1000;

function HudStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <Label className="text-faint">{label}</Label>
      <span className="font-mono text-[11px] text-ink">{value}</span>
    </div>
  );
}

export function EarthView({ level }: { level: AlertLevel }) {
  const telemetry = useTelemetryStore((s) => s.telemetry);
  const [mode, setMode] = useState<Mode>("pending");
  const [pass, setPass] = useState<PassWindow | null>(null);
  const [remaining, setRemaining] = useState<number | null>(null);
  const lowFps = useRef(0);

  // Decide 3D vs 2D after mount (WebGL + reduced-motion aware).
  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setMode(!reduced && supportsWebGL() ? "3d" : "2d");
  }, []);

  // Ground-station pass schedule.
  useEffect(() => {
    const compute = () => setPass(nextPass(GROUND_STATIONS[0], Date.now() / 1000));
    compute();
    const id = setInterval(compute, 20000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!pass) return;
    const tick = () => setRemaining(pass.startT - Date.now() / 1000);
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [pass]);

  // Auto-fallback to 2D if the 3D scene can't hold frame rate.
  const onFps = (fps: number) => {
    if (mode !== "3d") return;
    if (fps < 38) {
      lowFps.current += 1;
      if (lowFps.current >= 2) setMode("2d");
    } else {
      lowFps.current = 0;
    }
  };

  const sunlit = telemetry ? !telemetry.in_eclipse : true;
  const sunFraction = telemetry?.sun_fraction ?? 1;
  const station = GROUND_STATIONS[0];

  let passText = "--:--";
  let passLabel = "Next pass";
  if (pass) {
    if (remaining !== null && remaining > 0) {
      passText = formatDuration(remaining);
    } else if (remaining !== null && remaining <= 0 && remaining > -(pass.endT - pass.startT)) {
      passText = "AOS NOW";
      passLabel = "Ground contact";
    }
  }

  const alertTone =
    level === "CRITICAL"
      ? "rgba(255,91,107,0.5)"
      : level === "WARNING"
        ? "rgba(255,181,71,0.42)"
        : "transparent";

  return (
    <Panel glow className="relative flex min-h-0 flex-col overflow-hidden">
      {/* alert wash */}
      <div
        className="pointer-events-none absolute inset-0 z-10 transition-opacity duration-700"
        style={{
          background: `radial-gradient(120% 90% at 50% 45%, transparent 55%, ${alertTone})`,
          opacity: level === "NOMINAL" ? 0 : 1,
        }}
      />

      <div className="z-20 flex items-center justify-between px-4 pt-3">
        <div className="flex items-center gap-2">
          <Satellite className="size-3.5 text-accent" />
          <Label className="text-muted">Nadir Earth View</Label>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge tone={sunlit ? "nominal" : "muted"} dot={false}>
            {sunlit ? "Sunlit" : "Eclipse"}
          </StatusBadge>
          <span className="font-mono text-[10px] text-faint">
            ILLUM {(sunFraction * 100).toFixed(0)}%
          </span>
        </div>
      </div>

      <div className="relative z-20 min-h-0 flex-1">
        {mode === "3d" && <Earth3D level={level} onFps={onFps} />}
        {mode === "2d" && <OrbitMap2D level={level} />}
        {mode === "pending" && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-faint">
              Initialising globe
            </span>
          </div>
        )}

        {/* corner brackets */}
        <div className="pointer-events-none absolute inset-3 z-20">
          {[
            "left-0 top-0",
            "right-0 top-0 rotate-90",
            "bottom-0 left-0 -rotate-90",
            "bottom-0 right-0 rotate-180",
          ].map((cls) => (
            <span key={cls} className={`absolute size-4 ${cls}`}>
              <span className="absolute left-0 top-0 h-px w-3 bg-accent/40" />
              <span className="absolute left-0 top-0 h-3 w-px bg-accent/40" />
            </span>
          ))}
        </div>

        {/* next-pass countdown */}
        <div className="absolute bottom-3 left-3 z-20 rounded-md border border-hairline-2 bg-base/60 px-3 py-2 backdrop-blur">
          <Label className="text-faint">{passLabel}</Label>
          <div className="mt-0.5 flex items-baseline gap-2">
            <span className="font-mono text-sm tabular-nums text-accent">
              {passText}
            </span>
            <span className="font-mono text-[10px] text-muted">
              {station.id} · {station.name}
            </span>
          </div>
        </div>

        {/* orbital params */}
        <div className="absolute bottom-3 right-3 z-20 flex gap-4 rounded-md border border-hairline-2 bg-base/60 px-3 py-2 backdrop-blur">
          <HudStat label="Alt" value={`${MISSION.altitudeKm} km`} />
          <HudStat label="Inc" value={`${MISSION.inclinationDeg.toFixed(1)}°`} />
          <HudStat label="Vel" value={`${ORBITAL_VELOCITY.toFixed(2)} km/s`} />
          <HudStat label="Period" value={formatDuration(ORBIT_PERIOD_S)} />
        </div>

        {/* render-mode chip */}
        <span className="absolute bottom-3 left-1/2 z-20 -translate-x-1/2 rounded-md border border-hairline-2 bg-base/60 px-2.5 py-1 font-mono text-[9px] uppercase tracking-[0.24em] text-faint backdrop-blur">
          {mode === "3d" ? "3D · WebGL" : mode === "2d" ? "2D ground track" : "standby"}
        </span>

        {level !== "NOMINAL" && (
          <span
            className={`absolute right-3 top-3 z-20 animate-pulse rounded border px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.2em] backdrop-blur ${
              level === "CRITICAL"
                ? "border-critical/40 bg-critical/10 text-critical"
                : "border-warning/40 bg-warning/10 text-warning"
            }`}
          >
            {level === "CRITICAL" ? "critical" : "anomaly"}
          </span>
        )}
      </div>
    </Panel>
  );
}
