/** Wire contract mirrors backend/models/schemas.py exactly. */

export type Severity = "info" | "warning" | "critical";
export type SystemStatus = "NOMINAL" | "WARNING" | "CRITICAL";
export type Sensor =
  | "battery_voltage"
  | "battery_temp"
  | "solar_current"
  | "bus_temp"
  | "signal_strength"
  | "gyro_rate"
  | "payload_power";
export type PayloadMode = "idle" | "imaging" | "downlink";
export type DetectorKind =
  | "rule"
  | "zscore"
  | "rate"
  | "correlated"
  | "iforest";
export type AnomalyKind =
  | "out_of_range"
  | "rate_of_change"
  | "drift"
  | "signal_degradation"
  | "correlated"
  | "ml_anomaly";
export type AgentPhase = "observe" | "reason" | "plan" | "verify";
export type ActionStatus = "proposed" | "executing" | "succeeded" | "failed";

export type LinkState = "NO_CONTACT" | "ACQUIRING" | "CONNECTED";
export type PassEvent = "next_pass" | "aos" | "los";
export type CommandStatus =
  | "PENDING_APPROVAL"
  | "APPROVED"
  | "QUEUED_FOR_PASS"
  | "UPLINKED"
  | "EXECUTED";
export type CommandAction = "submit" | "approve" | "reject";

export interface TelemetryReading {
  timestamp: string;
  battery_voltage: number;
  battery_temp: number;
  solar_current: number;
  bus_temp: number;
  signal_strength: number;
  gyro_rate: number;
  payload_power: number;
  in_eclipse: boolean;
  sun_fraction: number;
  payload_mode: PayloadMode;
}

export interface AnomalyMessage {
  anomaly_id: string;
  sensor: Sensor;
  kind: AnomalyKind;
  severity: Severity;
  value: number;
  expected_min: number | null;
  expected_max: number | null;
  baseline: number | null;
  confidence: number;
  detected_at: string;
  description: string;
  detector_type: DetectorKind | null;
  subsystem: string | null;
  incident_id: string | null;
  /** ML confidence 0..1 (higher = more anomalous); null for rule/statistical. */
  anomaly_score: number | null;
  /** Sensors that contributed most to an ML anomaly (explainability). */
  contributing_sensors: string[];
}

export interface AgentThinkingMessage {
  step: number;
  phase: AgentPhase;
  content: string;
  inputs: Record<string, unknown>;
  elapsed_ms: number | null;
}

export interface AgentActionMessage {
  action_id: string;
  action: string;
  target: string | null;
  params: Record<string, unknown>;
  rationale: string;
  status: ActionStatus;
  created_at: string;
}

export interface StatusUpdateMessage {
  source: "system" | "detection" | "agent" | "operator";
  level: Severity;
  status: string;
  message: string;
}

export interface ReportSection {
  heading: string;
  body: string;
}

export interface ReportReadyMessage {
  report_id: string;
  title: string;
  summary: string;
  severity: Severity;
  sections: ReportSection[];
  related_anomaly_ids: string[];
  generated_at: string;
}

export interface LinkStatusMessage {
  simulated: true;
  link_state: LinkState;
  /** 0..1, derived from elevation and link faults. */
  signal_quality: number;
  packets_received: number;
  packets_lost: number;
  crc_failures: number;
  buffer_depth: number;
  last_contact_time: string | null;
  aos: string | null;
  los_expected: string | null;
}

export interface PassWindowModel {
  pass_id: string;
  aos: string;
  los: string;
  duration_s: number;
  max_elevation_deg: number;
}

export interface PassInfoMessage {
  simulated: true;
  event: PassEvent;
  station: string;
  time_scale: number;
  window: PassWindowModel | null;
  /** Sim-time countdown to the next acquisition of signal. */
  seconds_to_aos: number | null;
}

export interface CommandStatusMessage {
  command_id: string;
  command: string;
  params: Record<string, unknown>;
  status: CommandStatus;
  detail: string;
  submitted_by: string;
  approved_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface CommandQueueCounts {
  pending_approval: number;
  approved: number;
  queued_for_pass: number;
  uplinked: number;
  executed: number;
  total: number;
}

export type MessageType =
  | "telemetry"
  | "anomaly"
  | "agent_thinking"
  | "agent_action"
  | "status_update"
  | "report_ready"
  | "link_status"
  | "pass_info"
  | "command_status";

export interface Envelope<T, K extends MessageType> {
  type: K;
  seq: number;
  ts: string;
  payload: T;
}

export type WSMessage =
  | Envelope<TelemetryReading, "telemetry">
  | Envelope<AnomalyMessage, "anomaly">
  | Envelope<AgentThinkingMessage, "agent_thinking">
  | Envelope<AgentActionMessage, "agent_action">
  | Envelope<StatusUpdateMessage, "status_update">
  | Envelope<ReportReadyMessage, "report_ready">
  | Envelope<LinkStatusMessage, "link_status">
  | Envelope<PassInfoMessage, "pass_info">
  | Envelope<CommandStatusMessage, "command_status">;

/** Client -> server frames (mirrors backend `UplinkCommandRequest`). */
export type ClientFrame =
  | {
      type: "command_request";
      action: "submit";
      command: string;
      params?: Record<string, unknown>;
      operator?: string;
    }
  | {
      type: "command_request";
      action: "approve" | "reject";
      command_id: string;
      operator?: string;
    };

export interface HealthResponse {
  status: "ok" | "degraded";
  service: string;
  version: string;
  uptime_s: number;
  clients_connected: number;
  readings_generated: number;
  readings_dropped: number;
  frames_delivered: number;
  active_faults: string[];
  telemetry_hz: number;
  subsystems: Record<string, SystemStatus>;
  anomalies_raised: number;
  agent_enabled: boolean;
  agent_incidents_handled: number;
  agent_active_incidents: number;
  agent_actions_taken: number;
  agent_reports_generated: number;
  agent_thinking_steps: number;
  last_telemetry_age_s: number | null;
  time_scale: number;
  ground_station: string;
  link_state: LinkState;
  signal_quality: number;
  buffer_depth: number;
  packets_received: number;
  packets_lost: number;
  crc_failures: number;
  packets_forwarded: number;
  last_contact_time: string | null;
  next_pass_aos: string | null;
  seconds_to_next_aos: number | null;
  next_pass_max_elevation_deg: number | null;
  downlink_bps: number;
  command_queue: CommandQueueCounts;
}

const KNOWN: ReadonlySet<string> = new Set<MessageType>([
  "telemetry",
  "anomaly",
  "agent_thinking",
  "agent_action",
  "status_update",
  "report_ready",
  "link_status",
  "pass_info",
  "command_status",
]);

/** Parse + minimally validate a raw socket frame. Returns null if unusable. */
export function parseMessage(raw: string): WSMessage | null {
  try {
    const data = JSON.parse(raw) as { type?: string; payload?: unknown };
    if (!data || typeof data.type !== "string") return null;
    if (!KNOWN.has(data.type)) return null;
    return data as WSMessage;
  } catch {
    return null;
  }
}
