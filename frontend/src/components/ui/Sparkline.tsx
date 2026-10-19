"use client";

import { useEffect, useRef } from "react";

import type { Tone } from "./StatusBadge";

const TONE_HEX: Record<Tone, string> = {
  nominal: "#57e6a8",
  warning: "#ffb547",
  critical: "#ff5b6b",
  accent: "#46d8ff",
  muted: "#56657f",
};

export function Sparkline({
  data,
  tone = "accent",
  className,
}: {
  data: number[];
  tone?: Tone;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, canvas.clientWidth);
    const h = Math.max(1, canvas.clientHeight);
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const color = TONE_HEX[tone];
    const n = data.length;
    if (n < 2) {
      ctx.strokeStyle = "rgba(120,150,190,0.18)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, h - 0.5);
      ctx.lineTo(w, h - 0.5);
      ctx.stroke();
      return;
    }

    let min = Infinity;
    let max = -Infinity;
    for (const v of data) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
    const span = max - min || 1;
    const pad = 2;
    const x = (i: number) => (i / (n - 1)) * w;
    const y = (v: number) => pad + (1 - (v - min) / span) * (h - pad * 2);

    ctx.beginPath();
    ctx.moveTo(x(0), y(data[0]));
    for (let i = 1; i < n; i += 1) ctx.lineTo(x(i), y(data[i]));

    ctx.lineTo(w, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, `${color}33`);
    grad.addColorStop(1, `${color}00`);
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(x(0), y(data[0]));
    for (let i = 1; i < n; i += 1) ctx.lineTo(x(i), y(data[i]));
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.4;
    ctx.lineJoin = "round";
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(x(n - 1), y(data[n - 1]), 1.8, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }, [data, tone]);

  return <canvas ref={ref} className={className} />;
}
