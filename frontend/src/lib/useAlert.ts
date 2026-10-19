"use client";

import { useEffect, useMemo, useState } from "react";

import { useTelemetryStore, type AnomalyEvent } from "./store";
import type { SystemStatus } from "./ws";

export type AlertLevel = "NOMINAL" | "WARNING" | "CRITICAL";

export interface AlertState {
  level: AlertLevel;
  subsystem: string | null;
  cause: string | null;
  /** increments on every level/subsystem transition, to retrigger animations */
  tick: number;
}

const RANK: Record<AlertLevel, number> = { NOMINAL: 0, WARNING: 1, CRITICAL: 2 };
const ANOMALY_TTL_MS = 15_000;
const EMPTY: string[] = [];

export const FAULT_SUBSYSTEM: Record<string, string> = {
  battery_overheat: "Thermal",
  solar_degradation: "Power",
  signal_loss: "Comms",
};

function levelOf(severity: string): AlertLevel {
  if (severity === "critical") return "CRITICAL";
  if (severity === "warning") return "WARNING";
  return "NOMINAL";
}

export function computeAlert(
  subsystems: Record<string, SystemStatus>,
  lastAnomaly: AnomalyEvent | null,
  activeFaults: string[],
  nowMs: number,
): Omit<AlertState, "tick"> {
  let level: AlertLevel = "NOMINAL";
  let subsystem: string | null = null;
  let cause: string | null = null;

  for (const [name, status] of Object.entries(subsystems)) {
    if (RANK[status] > RANK[level]) {
      level = status;
      subsystem = name;
      cause = "subsystem status";
    }
  }

  if (lastAnomaly && nowMs - lastAnomaly.at < ANOMALY_TTL_MS) {
    const lvl = levelOf(lastAnomaly.severity);
    if (lvl !== "NOMINAL" && RANK[lvl] >= RANK[level]) {
      level = lvl;
      if (lastAnomaly.subsystem) subsystem = lastAnomaly.subsystem;
      cause = "detector anomaly";
    }
  }

  if (activeFaults.length > 0 && RANK[level] < RANK.WARNING) {
    level = "WARNING";
    subsystem = subsystem ?? FAULT_SUBSYSTEM[activeFaults[0]] ?? null;
    cause = "fault armed";
  }

  return { level, subsystem, cause };
}

function useTick(ms: number): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now || Date.now();
}

export function useAlert(): AlertState {
  const subsystems = useTelemetryStore((s) => s.subsystems);
  const lastAnomaly = useTelemetryStore((s) => s.lastAnomaly);
  const activeFaults = useTelemetryStore((s) => s.health?.active_faults ?? EMPTY);
  const now = useTick(1000);

  const computed = useMemo(
    () => computeAlert(subsystems, lastAnomaly, activeFaults, now),
    [subsystems, lastAnomaly, activeFaults, now],
  );

  const [tick, setTick] = useState(0);
  useEffect(() => {
    setTick((t) => t + 1);
  }, [computed.level, computed.subsystem]);

  return { ...computed, tick };
}
