export function fmtNumber(
  value: number | null | undefined,
  decimals = 2,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return value.toFixed(decimals);
}

export function fmtSigned(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  const s = value.toFixed(decimals);
  return value > 0 ? `+${s}` : s;
}

export function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

export function fmtClock(date: Date): string {
  return `${pad2(date.getUTCHours())}:${pad2(date.getUTCMinutes())}:${pad2(
    date.getUTCSeconds(),
  )}`;
}

export function fmtDateUTC(date: Date): string {
  return `J${String(date.getUTCDate()).padStart(3, "0")} ${date
    .toISOString()
    .slice(0, 10)}`;
}

export function fmtUptime(seconds: number | null | undefined): string {
  if (!seconds || seconds < 0) return "00:00:00";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${pad2(h)}:${pad2(m)}:${pad2(sec)}`;
}

export function fmtAge(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return "--";
  const s = Math.floor(ms / 1000);
  if (s < 1) return "now";
  if (s < 60) return `${s}s ago`;
  return `${Math.floor(s / 60)}m ago`;
}

/** Format a countdown as mm:ss (clamped at 0). */
export function fmtCountdown(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "--:--";
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  return `${pad2(m)}:${pad2(s % 60)}`;
}
