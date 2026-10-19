"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Orbit } from "lucide-react";

import { MISSION } from "@/lib/config";
import { fmtClock } from "@/lib/format";
import { useTelemetryStore } from "@/lib/store";
import type { AlertState } from "@/lib/useAlert";
import { cn } from "@/lib/cn";
import { Label } from "./ui/Label";
import { StatusBadge, StatusDot } from "./ui/StatusBadge";

function useUtcClock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

const ALERT_STYLE = {
  NOMINAL: { border: "rgba(60,214,150,0.3)", bg: "rgba(60,214,150,0.1)", text: "#3cd696" },
  WARNING: { border: "rgba(255,181,71,0.4)", bg: "rgba(255,181,71,0.12)", text: "#ffb547" },
  CRITICAL: { border: "rgba(255,91,107,0.45)", bg: "rgba(255,91,107,0.14)", text: "#ff5b6b" },
} as const;

function OverallBadge({ alert }: { alert: AlertState }) {
  const style = ALERT_STYLE[alert.level];
  const label =
    alert.level === "NOMINAL" ? "ALL SYSTEMS NOMINAL" : `SYSTEM ${alert.level}`;
  return (
    <motion.span
      className="inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-[10px] font-medium uppercase tracking-[0.14em]"
      animate={{
        borderColor: style.border,
        backgroundColor: style.bg,
        color: style.text,
      }}
      transition={{ duration: 0.5 }}
    >
      <StatusDot
        tone={
          alert.level === "CRITICAL"
            ? "critical"
            : alert.level === "WARNING"
              ? "warning"
              : "nominal"
        }
        animate={alert.level !== "NOMINAL"}
      />
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={label}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.25 }}
        >
          {label}
        </motion.span>
      </AnimatePresence>
      {alert.subsystem && alert.level !== "NOMINAL" && (
        <span className="font-mono text-[9px] opacity-70">· {alert.subsystem}</span>
      )}
    </motion.span>
  );
}

function AlertSweep({ alert }: { alert: AlertState }) {
  const color = alert.level === "CRITICAL" ? "#ff5b6b" : "#ffb547";
  return (
    <AnimatePresence>
      {alert.level !== "NOMINAL" && (
        <motion.div
          key={alert.tick}
          className="pointer-events-none absolute inset-0 overflow-hidden"
          initial={{ opacity: 0.9 }}
          animate={{ opacity: 0 }}
          transition={{ duration: 1.4, ease: "easeOut" }}
        >
          <motion.span
            className="absolute top-0 h-full w-1/3"
            style={{
              background: `linear-gradient(90deg, transparent, ${color}, transparent)`,
              filter: "blur(2px)",
            }}
            initial={{ x: "-120%" }}
            animate={{ x: "360%" }}
            transition={{ duration: 1.3, ease: "easeInOut" }}
          />
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function TopStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <Label className="text-faint">{label}</Label>
      <span className="font-mono text-xs text-ink">{value}</span>
    </div>
  );
}

export function TopBar({ alert }: { alert: AlertState }) {
  const now = useUtcClock();
  const connection = useTelemetryStore((s) => s.connection);
  const telemetry = useTelemetryStore((s) => s.telemetry);

  return (
    <header className="relative flex h-14 shrink-0 items-center gap-6 px-1">
      <AlertSweep alert={alert} />

      <div className="flex items-center gap-3">
        <div className="relative flex size-9 items-center justify-center rounded-lg border border-hairline-2 bg-white/[0.02]">
          <Orbit className="size-4 text-accent" />
          <span className="absolute -right-0.5 -top-0.5">
            <StatusDot
              tone={
                alert.level === "CRITICAL"
                  ? "critical"
                  : alert.level === "WARNING"
                    ? "warning"
                    : "nominal"
              }
              animate={alert.level !== "NOMINAL"}
            />
          </span>
        </div>
        <div className="flex flex-col leading-tight">
          <div className="flex items-baseline gap-2">
            <span className="text-sm font-semibold tracking-[0.14em] text-ink">
              {MISSION.vehicle}
            </span>
            <span className="font-mono text-[11px] text-accent">/ {MISSION.regime}</span>
          </div>
          <Label className="text-faint">Mission Control</Label>
        </div>
      </div>

      <div className="hidden h-8 items-center gap-6 border-l border-hairline pl-6 xl:flex">
        <TopStat label="Altitude" value={`${MISSION.altitudeKm} KM`} />
        <TopStat label="Inclination" value={`${MISSION.inclinationDeg.toFixed(1)}\u00B0`} />
        <TopStat label="Payload" value={telemetry?.payload_mode?.toUpperCase() ?? "--"} />
        <TopStat label="Illumination" value={telemetry?.in_eclipse ? "ECLIPSE" : "SUNLIT"} />
      </div>

      <div className="ml-auto flex items-center gap-5">
        <OverallBadge alert={alert} />
        <StatusBadge
          tone={connection === "live" ? "nominal" : "warning"}
          animateDot={connection !== "live"}
          className="h-7 px-2.5"
        >
          {connection === "live"
            ? "LINK LIVE"
            : connection === "connecting"
              ? "LINKING"
              : connection === "offline"
                ? "LINK LOST"
                : "RECONNECTING"}
        </StatusBadge>
        <div className="flex flex-col items-end">
          <Label className="text-faint">UTC</Label>
          <span
            className={cn(
              "font-mono text-lg leading-none tabular-nums text-ink",
              !now && "text-faint",
            )}
          >
            {now ? fmtClock(now) : "--:--:--"}
          </span>
        </div>
      </div>
    </header>
  );
}
