"use client";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/cn";
import { SAMPLE_EASE_MS } from "@/lib/config";
import { fmtNumber } from "@/lib/format";
import type { SensorSpec } from "@/lib/sensors";
import { useTelemetryStore, type SensorMarker } from "@/lib/store";
import { Label } from "./ui/Label";

const PAD_L = 10;
const PAD_R = 8;
const PAD_T = 10;
const PAD_B = 8;

const SEVERITY_COLOR: Record<string, string> = {
  info: "rgba(70,216,255,0.9)",
  warning: "rgba(255,181,71,0.95)",
  critical: "rgba(255,91,107,0.95)",
};

function easeOutCubic(x: number): number {
  return 1 - Math.pow(1 - x, 3);
}

interface DrawArgs {
  ctx: CanvasRenderingContext2D;
  w: number;
  h: number;
  spec: SensorSpec;
  values: number[];
  times: number[];
  markers: SensorMarker[];
  now: number;
  lastMessageAt: number;
}

function draw({ ctx, w, h, spec, values, times, markers, now, lastMessageAt }: DrawArgs) {
  ctx.clearRect(0, 0, w, h);

  const plotW = Math.max(1, w - PAD_L - PAD_R);
  const plotH = Math.max(1, h - PAD_T - PAD_B);
  const [dmin, dmax] = spec.domain;
  const y = (v: number) =>
    PAD_T + (1 - (Math.min(Math.max(v, dmin), dmax) - dmin) / (dmax - dmin)) * plotH;

  // --- threshold bands -----------------------------------------------------
  ctx.save();
  if (spec.direction === "high") {
    ctx.fillStyle = "rgba(255,181,71,0.06)";
    ctx.fillRect(PAD_L, y(spec.crit), plotW, y(spec.warn) - y(spec.crit));
    ctx.fillStyle = "rgba(255,91,107,0.08)";
    ctx.fillRect(PAD_L, PAD_T, plotW, y(spec.crit) - PAD_T);
  } else {
    ctx.fillStyle = "rgba(255,181,71,0.06)";
    ctx.fillRect(PAD_L, y(spec.warn), plotW, y(spec.crit) - y(spec.warn));
    ctx.fillStyle = "rgba(255,91,107,0.08)";
    ctx.fillRect(PAD_L, y(spec.crit), plotW, PAD_T + plotH - y(spec.crit));
  }
  ctx.restore();

  // --- grid ----------------------------------------------------------------
  ctx.strokeStyle = "rgba(130,170,230,0.07)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const gy = PAD_T + (i / 4) * plotH;
    ctx.beginPath();
    ctx.moveTo(PAD_L, gy);
    ctx.lineTo(PAD_L + plotW, gy);
    ctx.stroke();
  }

  // --- warn / crit dashed lines -------------------------------------------
  const drawLimit = (value: number, color: string, text: string) => {
    const ly = y(value);
    ctx.save();
    ctx.setLineDash([3, 4]);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(PAD_L, ly);
    ctx.lineTo(PAD_L + plotW, ly);
    ctx.stroke();
    ctx.restore();
    ctx.font =
      "9px var(--font-jetbrains-mono, ui-monospace), monospace";
    ctx.fillStyle = color;
    ctx.textAlign = "right";
    ctx.textBaseline = "bottom";
    ctx.fillText(text, PAD_L + plotW, y(value) - 2);
  };
  drawLimit(spec.warn, "rgba(255,181,71,0.5)", `WARN ${fmtNumber(spec.warn, spec.decimals)}`);
  drawLimit(spec.crit, "rgba(255,91,107,0.6)", `CRIT ${fmtNumber(spec.crit, spec.decimals)}`);

  const n = values.length;

  if (n < 2) {
    ctx.fillStyle = "rgba(130,150,180,0.5)";
    ctx.font = "10px var(--font-jetbrains-mono), monospace";
    ctx.textAlign = "center";
    ctx.fillText("AWAITING TELEMETRY", PAD_L + plotW / 2, PAD_T + plotH / 2);
    return;
  }

  const denom = Math.max(n - 1, 30);
  const x = (i: number) => PAD_L + (i / denom) * plotW;

  // --- anomaly markers -----------------------------------------------------
  for (const marker of markers) {
    let bestIdx = 0;
    let bestDelta = Infinity;
    for (let i = 0; i < times.length; i += 1) {
      const d = Math.abs(times[i] - marker.t);
      if (d < bestDelta) {
        bestDelta = d;
        bestIdx = i;
      }
    }
    const mx = x(bestIdx);
    ctx.strokeStyle = SEVERITY_COLOR[marker.severity] ?? SEVERITY_COLOR.info;
    ctx.globalAlpha = 0.35;
    ctx.setLineDash([2, 3]);
    ctx.beginPath();
    ctx.moveTo(mx, PAD_T);
    ctx.lineTo(mx, PAD_T + plotH);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 0.95;
    ctx.beginPath();
    ctx.arc(mx, PAD_T - 1, 2, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  // --- series --------------------------------------------------------------
  // tip eases in so each new sample sweeps rather than jumps
  const tipEase =
    lastMessageAt > 0
      ? easeOutCubic(Math.min(1, (now - lastMessageAt) / SAMPLE_EASE_MS))
      : 1;
  const lastIdx = n - 1;
  const prevVal = values[lastIdx - 1];
  const tipVal = prevVal + (values[lastIdx] - prevVal) * tipEase;

  const path = new Path2D();
  path.moveTo(x(0), y(values[0]));
  for (let i = 1; i < lastIdx; i += 1) path.lineTo(x(i), y(values[i]));
  path.lineTo(x(lastIdx), y(tipVal));

  // gradient fill under the curve
  const grad = ctx.createLinearGradient(0, PAD_T, 0, PAD_T + plotH);
  grad.addColorStop(0, "rgba(70,216,255,0.20)");
  grad.addColorStop(1, "rgba(70,216,255,0.0)");
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(x(0), PAD_T + plotH);
  ctx.lineTo(x(0), y(values[0]));
  for (let i = 1; i < lastIdx; i += 1) ctx.lineTo(x(i), y(values[i]));
  ctx.lineTo(x(lastIdx), y(tipVal));
  ctx.lineTo(x(lastIdx), PAD_T + plotH);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();
  ctx.restore();

  ctx.save();
  ctx.shadowColor = "rgba(70,216,255,0.7)";
  ctx.shadowBlur = 8;
  ctx.strokeStyle = "#46d8ff";
  ctx.lineWidth = 1.5;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.stroke(path);
  ctx.restore();

  // head dot
  ctx.save();
  ctx.shadowColor = "rgba(70,216,255,0.9)";
  ctx.shadowBlur = 10;
  ctx.fillStyle = "#bff0ff";
  ctx.beginPath();
  ctx.arc(x(lastIdx), y(tipVal), 2.4, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function nearestStatus(spec: SensorSpec, value: number): {
  tone: "nominal" | "warning" | "critical";
  text: string;
} {
  if (spec.direction === "high") {
    if (value >= spec.crit) return { tone: "critical", text: "CRITICAL" };
    if (value >= spec.warn) return { tone: "warning", text: "WARNING" };
  } else {
    if (value <= spec.crit) return { tone: "critical", text: "CRITICAL" };
    if (value <= spec.warn) return { tone: "warning", text: "WARNING" };
  }
  return { tone: "nominal", text: "NOMINAL" };
}

export function SensorChart({ spec }: { spec: SensorSpec }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  const telemetry = useTelemetryStore((s) => s.telemetry);
  const value = telemetry ? telemetry[spec.key] : null;

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let width = 0;
    let height = 0;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = wrap.getBoundingClientRect();
      width = Math.max(1, Math.floor(rect.width));
      height = Math.max(1, Math.floor(rect.height));
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(wrap);
    resize();

    const loop = () => {
      const state = useTelemetryStore.getState();
      draw({
        ctx,
        w: width,
        h: height,
        spec,
        values: state.series[spec.key],
        times: state.times,
        markers: state.markers[spec.key],
        now: performance.now(),
        lastMessageAt: state.lastMessageAt,
      });
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, [spec]);

  const status = value !== null ? nearestStatus(spec, value) : null;
  const toneText =
    status?.tone === "critical"
      ? "text-critical"
      : status?.tone === "warning"
        ? "text-warning"
        : "text-ink";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between pb-1.5">
        <div className="flex items-center gap-2">
          <Label className="text-faint">{spec.short}</Label>
          <span className="text-[11px] text-muted">{spec.label}</span>
        </div>
        <div className="flex items-baseline gap-1.5">
          <span className={cn("font-mono text-sm tabular-nums", toneText)}>
            {value !== null ? fmtNumber(value, spec.decimals) : "--"}
          </span>
          <span className="font-mono text-[10px] text-faint">{spec.unit}</span>
        </div>
      </div>
      <div ref={wrapRef} className="relative min-h-0 flex-1">
        <canvas ref={canvasRef} className="absolute inset-0 block" />
      </div>
    </div>
  );
}
