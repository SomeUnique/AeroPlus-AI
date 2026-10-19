import type { Sensor } from "./ws";

export interface SensorSpec {
  key: Sensor;
  label: string;
  short: string;
  unit: string;
  subsystem: string;
  decimals: number;
  domain: [number, number];
  /** warning threshold and whether the bad direction is high or low */
  warn: number;
  crit: number;
  direction: "high" | "low";
  /** rule only valid while sunlit (solar array) */
  sunlit?: boolean;
}

export const SENSOR_SPECS: SensorSpec[] = [
  {
    key: "battery_voltage",
    label: "Battery Voltage",
    short: "V-BUS",
    unit: "V",
    subsystem: "Power",
    decimals: 2,
    domain: [23, 29],
    warn: 25.5,
    crit: 24.0,
    direction: "low",
  },
  {
    key: "solar_current",
    label: "Solar Array Current",
    short: "I-SOL",
    unit: "A",
    subsystem: "Power",
    decimals: 2,
    domain: [0, 7],
    warn: 4.3,
    crit: 3.0,
    direction: "low",
    sunlit: true,
  },
  {
    key: "battery_temp",
    label: "Battery Temperature",
    short: "T-BAT",
    unit: "\u00B0C",
    subsystem: "Thermal",
    decimals: 1,
    domain: [10, 60],
    warn: 48,
    crit: 55,
    direction: "high",
  },
  {
    key: "signal_strength",
    label: "Downlink RSSI",
    short: "RSSI",
    unit: "dBm",
    subsystem: "Comms",
    decimals: 1,
    domain: [-112, -52],
    warn: -98,
    crit: -108,
    direction: "low",
  },
  {
    key: "gyro_rate",
    label: "Body Rate",
    short: "GYRO",
    unit: "\u00B0/s",
    subsystem: "ADCS",
    decimals: 3,
    domain: [0, 0.6],
    warn: 0.3,
    crit: 0.5,
    direction: "high",
  },
  {
    key: "payload_power",
    label: "Payload Draw",
    short: "P-PAY",
    unit: "W",
    subsystem: "Payload",
    decimals: 1,
    domain: [0, 65],
    warn: 55,
    crit: 60,
    direction: "high",
  },
];

export const SENSOR_BY_KEY: Record<string, SensorSpec> = SENSOR_SPECS.reduce(
  (acc, spec) => {
    acc[spec.key] = spec;
    return acc;
  },
  {} as Record<string, SensorSpec>,
);

export const SUBSYSTEM_ORDER = [
  "Power",
  "Thermal",
  "Comms",
  "Payload",
  "ADCS",
] as const;
