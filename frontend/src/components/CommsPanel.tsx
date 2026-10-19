"use client";

import { Radio, Timer } from "lucide-react";

import { cn } from "@/lib/cn";
import { fmtCountdown, fmtNumber } from "@/lib/format";
import { useTelemetryStore } from "@/lib/store";
import { usePassCountdown } from "@/lib/usePassCountdown";
import type { LinkState } from "@/lib/ws";
import { Label } from "./ui/Label";
import { Panel } from "./ui/Panel";
import { StatusBadge, StatusDot, type Tone } from "./ui/StatusBadge";
import { UplinkQueue } from "./UplinkQueue";

const LINK_TONE: Record<LinkState, Tone> = {
  CONNECTED: "nominal",
  ACQUIRING: "warning",
  NO_CONTACT: "muted",
};

function qualityTone(quality: number): Tone {
  if (quality >= 0.6) return "nominal";
  if (quality >= 0.3) return "warning";
  return "critical";
}

const BAR_TONE: Record<Tone, string> = {
  nominal: "bg-nominal",
  warning: "bg-warning",
  critical: "bg-critical",
  accent: "bg-accent",
  muted: "bg-faint",
};

function Metric({ label, value, tone }: { label: string; value: number; tone?: Tone }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-faint">
        {label}
      </span>
      <span
        className={cn(
          "font-mono text-[13px] leading-none",
          tone === "warning"
            ? "text-warning"
            : tone === "critical"
              ? "text-critical"
              : "text-ink",
        )}
      >
        {value}
      </span>
    </div>
  );
}

export function CommsPanel() {
  const link = useTelemetryStore((s) => s.linkStatus);
  const passInfo = useTelemetryStore((s) => s.passInfo);
  const countdown = usePassCountdown();

  const state: LinkState = link?.link_state ?? "NO_CONTACT";
  const tone = LINK_TONE[state];
  const quality = link?.signal_quality ?? 0;
  const qTone = qualityTone(quality);

  const milestone = countdown.inPass
    ? countdown.milestone === "los"
      ? "LOS IN"
      : "IN PASS"
    : countdown.milestone === "aos"
      ? "AOS IN"
      : "STANDBY";
  const showClock =
    countdown.seconds !== null &&
    (countdown.inPass ? countdown.milestone === "los" : countdown.milestone === "aos");

  return (
    <Panel glow className="flex min-h-0 flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-3.5 pb-2 pt-3">
        <div className="flex min-w-0 items-center gap-2">
          <Radio className="size-3.5 shrink-0 text-accent" />
          <Label className="text-muted">Ground Link</Label>
          <span className="shrink-0 rounded border border-hairline-2 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-widest text-faint">
            Simulated
          </span>
        </div>
        <StatusBadge tone={tone} animateDot={state === "CONNECTED"}>
          {state.replace("_", " ")}
        </StatusBadge>
      </div>

      <div className="hairline-h mx-3.5" />

      <div className="flex flex-col gap-2.5 px-3.5 py-2.5">
        <div className="flex items-center gap-2.5">
          <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-faint">
            Signal
          </span>
          <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className={cn("h-full rounded-full transition-[width] duration-500", BAR_TONE[qTone])}
              style={{ width: `${Math.round(quality * 100)}%` }}
            />
          </div>
          <span className="w-9 shrink-0 text-right font-mono text-[10px] text-muted">
            {Math.round(quality * 100)}%
          </span>
        </div>

        <div className="flex items-center justify-between">
          <Metric label="Rx" value={link?.packets_received ?? 0} />
          <Metric
            label="Lost"
            value={link?.packets_lost ?? 0}
            tone={link && link.packets_lost > 0 ? "warning" : undefined}
          />
          <Metric
            label="Crc"
            value={link?.crc_failures ?? 0}
            tone={link && link.crc_failures > 0 ? "warning" : undefined}
          />
          <Metric
            label="Buffer"
            value={link?.buffer_depth ?? 0}
            tone={link && link.buffer_depth > 256 ? "warning" : undefined}
          />
        </div>

        <div className="flex items-center gap-3 rounded-md border border-hairline/80 bg-white/[0.015] px-3 py-2">
          <Timer className="size-3.5 shrink-0 text-faint" />
          <div className="flex min-w-0 flex-col">
            <span className="font-mono text-[9px] uppercase tracking-[0.14em] text-faint">
              {milestone}
            </span>
            <span className="truncate text-[10px] text-muted">
              {passInfo?.station ?? "Karachi GS"}
              {passInfo?.window
                ? ` · max el ${fmtNumber(passInfo.window.max_elevation_deg, 1)}°`
                : ""}
            </span>
          </div>
          <span className="ml-auto flex items-center gap-1.5">
            <StatusDot tone={countdown.inPass ? "nominal" : "muted"} animate={countdown.inPass} />
            <span className="font-mono text-lg leading-none tracking-tight text-ink">
              {showClock ? fmtCountdown(countdown.seconds) : "—"}
            </span>
          </span>
        </div>
      </div>

      <div className="hairline-h mx-3.5" />

      <UplinkQueue />
    </Panel>
  );
}
