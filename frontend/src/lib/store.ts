import { create } from "zustand";

import { SERIES_CAPACITY } from "./config";
import type {
  AgentActionMessage,
  AgentPhase,
  AgentThinkingMessage,
  AnomalyMessage,
  CommandStatusMessage,
  HealthResponse,
  LinkStatusMessage,
  PassInfoMessage,
  ReportReadyMessage,
  Sensor,
  Severity,
  SystemStatus,
  TelemetryReading,
  WSMessage,
} from "./ws";

export const SENSOR_KEYS: Sensor[] = [
  "battery_voltage",
  "solar_current",
  "battery_temp",
  "signal_strength",
  "gyro_rate",
  "payload_power",
];

export interface SensorMarker {
  /** epoch ms of the triggering sample */
  t: number;
  severity: Severity;
  label: string;
}

export type ConnectionStatus = "connecting" | "live" | "reconnecting" | "offline";

export interface ActionRecord extends AgentActionMessage {
  receivedAt: number;
}

export interface AnomalyEvent {
  sensor: Sensor;
  subsystem: string | null;
  severity: Severity;
  at: number;
}

interface TelemetryState {
  connection: ConnectionStatus;
  attempt: number;
  lastMessageAt: number;
  connectedAt: number;

  telemetry: TelemetryReading | null;
  series: Record<Sensor, number[]>;
  times: number[];
  markers: Record<Sensor, SensorMarker[]>;

  subsystems: Record<string, SystemStatus>;
  lastChanged: Record<string, number>;
  lastAnomaly: AnomalyEvent | null;
  notices: { ts: number; message: string; level: Severity }[];

  thinking: AgentThinkingMessage[];
  actions: ActionRecord[];
  reports: ReportReadyMessage[];

  linkStatus: LinkStatusMessage | null;
  passInfo: PassInfoMessage | null;
  commands: CommandStatusMessage[];

  counts: {
    anomalies: number;
    actions: number;
    reports: number;
  };
  health: HealthResponse | null;

  ingest: (message: WSMessage) => void;
  setConnection: (status: ConnectionStatus, attempt?: number) => void;
  setHealth: (health: HealthResponse) => void;
  reset: () => void;
}

const emptySeries = (): Record<Sensor, number[]> =>
  SENSOR_KEYS.reduce(
    (acc, key) => {
      acc[key] = [];
      return acc;
    },
    {} as Record<Sensor, number[]>,
  );

const emptyMarkers = (): Record<Sensor, SensorMarker[]> =>
  SENSOR_KEYS.reduce(
    (acc, key) => {
      acc[key] = [];
      return acc;
    },
    {} as Record<Sensor, SensorMarker[]>,
  );

function pushCapped<T>(arr: T[], value: T, cap: number) {
  arr.push(value);
  if (arr.length > cap) arr.splice(0, arr.length - cap);
}

const PHASE_ORDER: Record<AgentPhase, number> = {
  observe: 0,
  reason: 1,
  plan: 2,
  verify: 3,
};

const STATUS_RE = /^([A-Za-z]+)_(NOMINAL|WARNING|CRITICAL)$/;

export const useTelemetryStore = create<TelemetryState>((set, get) => ({
  connection: "connecting",
  attempt: 0,
  lastMessageAt: 0,
  connectedAt: 0,

  telemetry: null,
  series: emptySeries(),
  times: [],
  markers: emptyMarkers(),

  subsystems: {},
  lastChanged: {},
  lastAnomaly: null,
  notices: [],

  thinking: [],
  actions: [],
  reports: [],

  linkStatus: null,
  passInfo: null,
  commands: [],

  counts: { anomalies: 0, actions: 0, reports: 0 },
  health: null,

  ingest: (message) => {
    const state = get();
    switch (message.type) {
      case "telemetry": {
        const reading = message.payload;
        const t = Date.parse(reading.timestamp) || Date.now();
        pushCapped(state.times, t, SERIES_CAPACITY);
        for (const key of SENSOR_KEYS) {
          pushCapped(state.series[key], reading[key], SERIES_CAPACITY);
        }
        set({ telemetry: reading, lastMessageAt: Date.now() });
        break;
      }
      case "anomaly": {
        const a: AnomalyMessage = message.payload;
        const t = Date.parse(message.ts) || Date.now();
        pushCapped(
          state.markers[a.sensor],
          { t, severity: a.severity, label: a.description },
          24,
        );
        set({
          counts: { ...state.counts, anomalies: state.counts.anomalies + 1 },
          lastAnomaly: {
            sensor: a.sensor,
            subsystem: a.subsystem ?? null,
            severity: a.severity,
            at: Date.now(),
          },
        });
        break;
      }
      case "agent_thinking": {
        pushCapped(state.thinking, message.payload, 160);
        set({ thinking: [...state.thinking] });
        break;
      }
      case "agent_action": {
        const incoming = message.payload;
        const next = state.actions.filter((a) => a.action_id !== incoming.action_id);
        next.push({ ...incoming, receivedAt: Date.now() });
        const isNew = !state.actions.some((a) => a.action_id === incoming.action_id);
        set({
          actions: next.slice(-40),
          counts: isNew
            ? { ...state.counts, actions: state.counts.actions + 1 }
            : state.counts,
        });
        break;
      }
      case "status_update": {
        const su = message.payload;
        if (su.status === "reset") {
          const cleared: Record<string, SystemStatus> = {};
          for (const name of Object.keys(state.subsystems)) cleared[name] = "NOMINAL";
          set({
            subsystems: cleared,
            lastAnomaly: null,
            lastChanged: {},
            linkStatus: null,
            passInfo: null,
            commands: [],
          });
          pushCapped(
            state.notices,
            { ts: Date.now(), message: su.message, level: su.level },
            12,
          );
          set({ notices: [...state.notices] });
          break;
        }
        const match = STATUS_RE.exec(su.status);
        if (match) {
          const name = match[1];
          const next = match[2] as SystemStatus;
          const changed = state.subsystems[name] !== next;
          const now = Date.now();
          set({
            subsystems: { ...state.subsystems, [name]: next },
            lastChanged: changed
              ? { ...state.lastChanged, [name]: now }
              : state.lastChanged,
          });
        } else {
          pushCapped(
            state.notices,
            { ts: Date.now(), message: su.message, level: su.level },
            12,
          );
          set({ notices: [...state.notices] });
        }
        break;
      }
      case "report_ready": {
        const next = [message.payload, ...state.reports].slice(0, 12);
        set({
          reports: next,
          counts: { ...state.counts, reports: state.counts.reports + 1 },
        });
        break;
      }
      case "link_status": {
        set({ linkStatus: message.payload, lastMessageAt: Date.now() });
        break;
      }
      case "pass_info": {
        set({ passInfo: message.payload });
        break;
      }
      case "command_status": {
        const incoming = message.payload;
        const existing = state.commands.findIndex(
          (c) => c.command_id === incoming.command_id,
        );
        const next = [...state.commands];
        if (existing >= 0) next[existing] = incoming;
        else next.push(incoming);
        set({ commands: next.slice(-40) });
        break;
      }
    }
  },

  setConnection: (status, attempt) =>
    set((s) => ({
      connection: status,
      attempt: attempt ?? s.attempt,
      connectedAt: status === "live" ? Date.now() : s.connectedAt,
    })),

  setHealth: (health) => set({ health }),

  reset: () =>
    set({
      telemetry: null,
      series: emptySeries(),
      times: [],
      markers: emptyMarkers(),
      subsystems: {},
      lastChanged: {},
      lastAnomaly: null,
      notices: [],
      thinking: [],
      actions: [],
      reports: [],
      linkStatus: null,
      passInfo: null,
      commands: [],
      counts: { anomalies: 0, actions: 0, reports: 0 },
    }),
}));

export { PHASE_ORDER };
