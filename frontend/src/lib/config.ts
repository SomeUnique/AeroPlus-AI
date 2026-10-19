/** Backend endpoints. Override via .env.local if the API runs elsewhere. */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ?? `${API_BASE.replace(/^http/, "ws")}/ws`;

export const MISSION = {
  vehicle: "AEROPULSE-1",
  regime: "LEO",
  altitudeKm: 550,
  inclinationDeg: 53.0,
} as const;

/** Rolling history kept per sensor (≈4 min at 1 Hz). */
export const SERIES_CAPACITY = 240;

/** How long the newest sample eases into place, in ms. */
export const SAMPLE_EASE_MS = 260;
