/**
 * Lightweight, deterministic orbit model for the mission-control visuals.
 *
 * This is an *aesthetic* circular-orbit propagator, not a flight dynamics tool:
 * it produces a plausible sun-synchronous-ish ground track and ground-station
 * pass timeline so the hero panel and the 2D fallback share one source of truth.
 */

import { MISSION } from "./config";

export const EARTH_RADIUS_KM = 6371;
export const MU_KM3_S2 = 398600.4418;
const EARTH_OMEGA = 7.2921159e-5; // rad/s, sidereal rotation
const DEG = 180 / Math.PI;

/** Scene units: the globe has radius 1, the orbit sits at 1 + alt/R. */
export const SCENE_EARTH_RADIUS = 1;
export const SCENE_ORBIT_RADIUS =
  SCENE_EARTH_RADIUS * (1 + MISSION.altitudeKm / EARTH_RADIUS_KM);

const semiMajorKm = EARTH_RADIUS_KM + MISSION.altitudeKm;
/** Kepler III: orbital period in seconds. */
export const ORBIT_PERIOD_S =
  2 * Math.PI * Math.sqrt((semiMajorKm * semiMajorKm * semiMajorKm) / MU_KM3_S2);
export const ORBIT_MEAN_MOTION = (2 * Math.PI) / ORBIT_PERIOD_S;
const INCLINATION = MISSION.inclinationDeg / DEG;

/** Fixed phases chosen so the track sweeps across interesting longitudes. */
const U0 = 0.55;
const RAAN = -1.9;
const LON0 = 18;

export interface SubPoint {
  lat: number;
  lon: number;
}

export interface GroundStation {
  id: string;
  name: string;
  lat: number;
  lon: number;
}

export const GROUND_STATIONS: GroundStation[] = [
  { id: "GS-01", name: "Svalbard", lat: 78.23, lon: 15.39 },
  { id: "GS-02", name: "Punta Arenas", lat: -53.16, lon: -70.91 },
  { id: "GS-03", name: "Awarua", lat: -46.53, lon: 168.38 },
];

function normalizeLon(lon: number): number {
  let value = ((lon + 180) % 360) - 180;
  if (value < -180) value += 360;
  return value;
}

/** Argument of latitude at absolute epoch-seconds `t`. */
export function argumentOfLatitude(t: number): number {
  return U0 + ORBIT_MEAN_MOTION * t;
}

/** Inertial position on the orbit ring, in scene units. */
export function orbitPosition(
  t: number,
  radius = SCENE_ORBIT_RADIUS,
): [number, number, number] {
  const u = argumentOfLatitude(t);
  const x = radius * Math.cos(u);
  const y = radius * Math.sin(u) * Math.cos(INCLINATION);
  const z = radius * Math.sin(u) * Math.sin(INCLINATION);
  return [x, y, z];
}

/** Sub-satellite point (earth-fixed lat/lon) at absolute epoch-seconds `t`. */
export function subSatellitePoint(t: number): SubPoint {
  const u = argumentOfLatitude(t);
  const z = Math.sin(u) * Math.sin(INCLINATION);
  const lat = Math.asin(z) * DEG;
  const lonRel = Math.atan2(Math.sin(u) * Math.cos(INCLINATION), Math.cos(u)) * DEG;
  const lon = normalizeLon(lonRel + RAAN * DEG + LON0 - EARTH_OMEGA * t * DEG);
  return { lat, lon };
}

/** A run of ground-track points spanning ±`spanS`, for drawing the path. */
export function groundTrack(
  t: number,
  spanS = ORBIT_PERIOD_S,
  count = 220,
): SubPoint[] {
  const points: SubPoint[] = [];
  for (let i = 0; i <= count; i += 1) {
    points.push(subSatellitePoint(t + (i / count) * spanS - spanS / 2));
  }
  return points;
}

/** Full orbit ring as scene-space points, matching `orbitPosition`. */
export function orbitRingPoints(count = 256): [number, number, number][] {
  const points: [number, number, number][] = [];
  for (let i = 0; i <= count; i += 1) {
    const u = (i / count) * Math.PI * 2;
    points.push([
      SCENE_ORBIT_RADIUS * Math.cos(u),
      SCENE_ORBIT_RADIUS * Math.sin(u) * Math.cos(INCLINATION),
      SCENE_ORBIT_RADIUS * Math.sin(u) * Math.sin(INCLINATION),
    ]);
  }
  return points;
}

/** Convert an earth-fixed sub-point to a scene-space point on the globe. */
export function subPointToScene(
  { lat, lon }: SubPoint,
  radius = SCENE_EARTH_RADIUS * 1.002,
): [number, number, number] {
  const phi = lat / DEG;
  const lambda = lon / DEG;
  return [
    radius * Math.cos(phi) * Math.cos(lambda),
    radius * Math.cos(phi) * Math.sin(lambda),
    radius * Math.sin(phi),
  ];
}

export interface PassWindow {
  station: GroundStation;
  startT: number;
  endT: number;
}

/** Great-circle central angle in degrees between two sub-points. */
export function centralAngle(a: SubPoint, b: SubPoint): number {
  const toRad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * toRad;
  const dLon = (b.lon - a.lon) * toRad;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * toRad) * Math.cos(b.lat * toRad) * Math.sin(dLon / 2) ** 2;
  return 2 * Math.asin(Math.min(1, Math.sqrt(h))) * DEG;
}

/**
 * Next moment the satellite rises above the station horizon mask. Scans the
 * model forward from `fromT`. `maskDeg` is the max central angle for a link.
 */
export function nextPass(
  station: GroundStation,
  fromT: number,
  maskDeg = 18,
  horizonS = ORBIT_PERIOD_S * 2,
  stepS = 4,
): PassWindow | null {
  const inView = (t: number) =>
    centralAngle(subSatellitePoint(t), station) <= maskDeg;

  let enteredAt: number | null = inView(fromT) ? fromT : null;
  if (enteredAt !== null) {
    // already in view: find the current pass end, then look for the next rise
    for (let t = fromT; t < fromT + horizonS; t += stepS) {
      if (!inView(t)) {
        enteredAt = null;
        break;
      }
    }
  }

  for (let t = fromT; t < fromT + horizonS; t += stepS) {
    if (inView(t)) {
      const startT = t;
      let endT = t;
      for (let e = t; e < t + horizonS && inView(e); e += stepS) endT = e;
      return { station, startT, endT };
    }
  }
  return null;
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}
