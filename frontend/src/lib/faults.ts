import { API_BASE } from "./config";

export interface FaultOption {
  type: string;
  label: string;
  subsystem: string;
  detail: string;
}

export const FAULT_OPTIONS: FaultOption[] = [
  {
    type: "battery_overheat",
    label: "Battery Overheat",
    subsystem: "Thermal",
    detail: "Cell temperature climbs while pack voltage sags (~90 s ramp)",
  },
  {
    type: "solar_degradation",
    label: "Solar Degradation",
    subsystem: "Power",
    detail: "Array output falls below predicted sunlight (~120 s ramp)",
  },
  {
    type: "signal_loss",
    label: "Signal Loss",
    subsystem: "Comms",
    detail: "Downlink RSSI fades out of the noise floor (~60 s ramp)",
  },
];

export async function injectFault(type: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/inject-fault`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ type }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function resetScenario(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/reset`, { method: "POST" });
    return res.ok;
  } catch {
    return false;
  }
}
