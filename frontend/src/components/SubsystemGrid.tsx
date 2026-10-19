"use client";

import { useEffect, useState } from "react";
import {
  Box,
  CircleCheck,
  Compass,
  OctagonAlert,
  Radio,
  ThermometerSun,
  TriangleAlert,
  Zap,
  type LucideIcon,
} from "lucide-react";

import { SENSOR_BY_KEY, SUBSYSTEM_ORDER } from "@/lib/sensors";
import { SENSOR_KEYS, useTelemetryStore } from "@/lib/store";
import type { AlertState } from "@/lib/useAlert";
import { fmtAge, fmtNumber } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Label } from "./ui/Label";
import { Panel } from "./ui/Panel";
import { Sparkline } from "./ui/Sparkline";
import { toneForStatus, type Tone } from "./ui/StatusBadge";
import { EmptyState } from "./ui/Skeleton";

const SUBSYSTEM_META: Record<
  string,
  { icon: LucideIcon; primary: string; secondary?: string }
> = {
  Power: { icon: Zap, primary: "battery_voltage", secondary: "solar_current" },
  Thermal: { icon: ThermometerSun, primary: "battery_temp" },
  Comms: { icon: Radio, primary: "signal_strength" },
  Payload: { icon: Box, primary: "payload_power" },
  ADCS: { icon: Compass, primary: "gyro_rate" },
};

const STATUS_ICON: Record<Tone, LucideIcon> = {
  nominal: CircleCheck,
  warning: TriangleAlert,
  critical: OctagonAlert,
  accent: CircleCheck,
  muted: CircleCheck,
};

const CHIP_CLASS: Record<Tone, string> = {
  nominal: "border-nominal/30 bg-nominal/10 text-nominal",
  warning: "border-warning/30 bg-warning/10 text-warning",
  critical: "border-critical/30 bg-critical/10 text-critical",
  accent: "border-accent/30 bg-accent/10 text-accent",
  muted: "border-hairline-2 bg-white/[0.02] text-muted",
};

function useNowTick(ms: number) {
  const [now, setNow] = useState(0);
  useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

function StatusChip({ status }: { status: string }) {
  const tone = toneForStatus(status as never);
  const Icon = STATUS_ICON[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-[0.12em]",
        CHIP_CLASS[tone],
      )}
    >
      <Icon className="size-3" />
      {status}
    </span>
  );
}

function SubsystemCard({
  subsystem,
  status,
  now,
  alerting,
}: {
  subsystem: string;
  status: string;
  now: number;
  alerting: boolean;
}) {
  const meta = SUBSYSTEM_META[subsystem];
  const telemetry = useTelemetryStore((s) => s.telemetry);
  const series = useTelemetryStore((s) => s.series);
  const changed = useTelemetryStore((s) => s.lastChanged[subsystem] ?? 0);

  if (!meta) return null;
  const Icon = meta.icon;
  const primary = SENSOR_BY_KEY[meta.primary];
  const secondary = meta.secondary ? SENSOR_BY_KEY[meta.secondary] : undefined;
  const tone = toneForStatus(status as never);
  const primaryValue = telemetry ? (telemetry[meta.primary as never] as number) : null;
  const secondaryValue =
    telemetry && secondary ? (telemetry[secondary.key as never] as number) : null;

  return (
    <div
      className={cn(
        "card flex min-h-0 flex-1 flex-col justify-between gap-1.5 p-3",
        alerting && status !== "NOMINAL" && "card-alert",
      )}
      data-status={status}
    >
      <div className="flex items-center gap-2">
        <span className="flex size-6 items-center justify-center rounded border border-hairline-2 bg-white/[0.02]">
          <Icon className="size-3.5 text-muted" />
        </span>
        <span className="text-[12px] font-medium tracking-wide text-ink">
          {subsystem}
        </span>
        <span className="ml-auto">
          <StatusChip status={status} />
        </span>
      </div>

      <Sparkline
        data={series[primary.key as never] ?? []}
        tone={tone === "muted" ? "accent" : tone}
        className="h-7 w-full"
      />

      <div className="flex items-end justify-between">
        <div className="flex items-baseline gap-1">
          <span className="font-mono text-base leading-none text-ink">
            {fmtNumber(primaryValue, primary.decimals)}
          </span>
          <span className="font-mono text-[10px] text-faint">{primary.unit}</span>
          <span className="ml-1 font-mono text-[9px] uppercase tracking-wider text-faint">
            {primary.short}
          </span>
        </div>
        <div className="flex flex-col items-end gap-0.5">
          {secondary && (
            <span className="font-mono text-[10px] text-muted">
              {secondary.short} {fmtNumber(secondaryValue, secondary.decimals)}
            </span>
          )}
          <span className="font-mono text-[9px] text-faint">
            {changed ? fmtAge(now - changed) : "no change"}
          </span>
        </div>
      </div>
    </div>
  );
}

export function SubsystemGrid({ alert }: { alert: AlertState }) {
  const subsystems = useTelemetryStore((s) => s.subsystems);
  const health = useTelemetryStore((s) => s.health);
  const now = useNowTick(5000);

  const known = SUBSYSTEM_ORDER.filter((s) => subsystems[s]);

  return (
    <Panel glow className="subsystems-panel flex min-h-0 flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 pb-2 pt-3">
        <div className="flex items-center gap-2">
          <Label className="text-muted">Subsystem Health</Label>
          {alert.level !== "NOMINAL" && alert.subsystem && (
            <span
              className={cn(
                "rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider",
                alert.level === "CRITICAL"
                  ? "border-critical/30 bg-critical/10 text-critical"
                  : "border-warning/30 bg-warning/10 text-warning",
              )}
            >
              {alert.subsystem}
            </span>
          )}
        </div>
        {health && (
          <span className="font-mono text-[10px] text-faint">
            {health.active_faults.length > 0
              ? `${health.active_faults.length} FAULT${
                  health.active_faults.length > 1 ? "S" : ""
                }`
              : "NO FAULTS"}
          </span>
        )}
      </div>

      <div className="hairline-h mx-4" />

      <div className="flex min-h-0 flex-1 flex-col gap-2 p-2.5">
        {known.length === 0 ? (
          <EmptyState
            className="flex-1"
            title="Acquiring subsystem telemetry"
            detail="Waiting for the first status frame from the spacecraft bus."
          />
        ) : (
          SUBSYSTEM_ORDER.map((subsystem) => (
            <SubsystemCard
              key={subsystem}
              subsystem={subsystem}
              status={subsystems[subsystem] ?? "NOMINAL"}
              now={now}
              alerting={alert.level !== "NOMINAL" && alert.subsystem === subsystem}
            />
          ))
        )}
      </div>

      <div className="hairline-h" />
      <div className="flex items-center justify-between px-4 py-2.5 font-mono text-[10px] text-faint">
        <span>
          INCIDENTS{" "}
          <span className="text-muted">{health?.agent_active_incidents ?? 0}</span>
        </span>
        <span>
          ANOMALIES{" "}
          <span className="text-muted">{health?.anomalies_raised ?? 0}</span>
        </span>
        <span>
          SENSORS <span className="text-muted">{SENSOR_KEYS.length}</span>
        </span>
      </div>
    </Panel>
  );
}
