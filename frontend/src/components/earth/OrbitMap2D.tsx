"use client";

import { useEffect, useRef } from "react";

import {
  GROUND_STATIONS,
  centralAngle,
  groundTrack,
  subSatellitePoint,
} from "@/lib/orbit";
import type { AlertLevel } from "@/lib/useAlert";

const ACCENT = "rgba(70,216,255";
const WARN = "rgba(255,181,71";
const CRIT = "rgba(255,91,107";

function tone(level: AlertLevel): string {
  if (level === "CRITICAL") return CRIT;
  if (level === "WARNING") return WARN;
  return ACCENT;
}

function project(lat: number, lon: number, w: number, h: number): [number, number] {
  return [((lon + 180) / 360) * w, ((90 - lat) / 180) * h];
}

function draw(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  level: AlertLevel,
) {
  ctx.clearRect(0, 0, w, h);
  const col = tone(level);

  // graticule
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(120,190,255,0.09)";
  for (let lon = -180; lon <= 180; lon += 30) {
    const [x] = project(0, lon, w, h);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    const [, y] = project(lat, 0, w, h);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }
  ctx.strokeStyle = "rgba(120,190,255,0.16)";
  const [, eqY] = project(0, 0, w, h);
  ctx.beginPath();
  ctx.moveTo(0, eqY);
  ctx.lineTo(w, eqY);
  ctx.stroke();

  // ground track
  const track = groundTrack(t, 5400, 260);
  ctx.strokeStyle = `${col},0.55)`;
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  let started = false;
  let prevLon = track[0].lon;
  for (const p of track) {
    const [x, y] = project(p.lat, p.lon, w, h);
    if (!started || Math.abs(p.lon - prevLon) > 180) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, y);
    }
    prevLon = p.lon;
  }
  ctx.stroke();

  // ground stations
  ctx.font = "9px ui-monospace, monospace";
  for (const s of GROUND_STATIONS) {
    const [x, y] = project(s.lat, s.lon, w, h);
    ctx.fillStyle = "rgba(70,216,255,0.85)";
    ctx.beginPath();
    ctx.arc(x, y, 2, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "rgba(70,216,255,0.35)";
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = "rgba(150,180,210,0.7)";
    ctx.fillText(s.id, x + 7, y + 3);
  }

  // sub-satellite point + coverage
  const sub = subSatellitePoint(t);
  const [sx, sy] = project(sub.lat, sub.lon, w, h);
  const near = centralAngle(sub, GROUND_STATIONS[0]) <= 30;

  ctx.save();
  ctx.beginPath();
  ctx.arc(sx, sy, 26, 0, Math.PI * 2);
  ctx.fillStyle = `${col},0.08)`;
  ctx.fill();
  ctx.strokeStyle = `${col},${near ? 0.6 : 0.25})`;
  ctx.stroke();
  ctx.restore();

  ctx.save();
  ctx.shadowColor = `${col},0.9)`;
  ctx.shadowBlur = 12;
  ctx.fillStyle = "#dff6ff";
  ctx.beginPath();
  ctx.arc(sx, sy, 3.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  if (near) {
    ctx.fillStyle = `${col},0.95)`;
    ctx.font = "9px ui-monospace, monospace";
    ctx.fillText("AOS", sx + 8, sy - 6);
  }
}

export function OrbitMap2D({ level }: { level: AlertLevel }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const levelRef = useRef<AlertLevel>(level);
  levelRef.current = level;

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
      draw(ctx, Math.max(1, wrap.clientWidth), Math.max(1, wrap.clientHeight), Date.now() / 1000, levelRef.current);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, []);

  return (
    <div ref={wrapRef} className="absolute inset-0">
      <canvas ref={canvasRef} className="block size-full" />
    </div>
  );
}
