"use client";

import { SENSOR_SPECS } from "@/lib/sensors";
import { Label } from "./ui/Label";
import { Panel } from "./ui/Panel";
import { SensorChart } from "./SensorChart";

export function SensorPanel() {
  return (
    <Panel glow className="flex min-h-0 flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 pb-2 pt-3">
        <Label className="text-muted">Sensor Array</Label>
        <span className="font-mono text-[10px] text-faint">1 Hz</span>
      </div>
      <div className="hairline-h mx-4" />
      <div className="flex min-h-0 flex-1 flex-col divide-y divide-hairline/60">
        {SENSOR_SPECS.map((spec) => (
          <div key={spec.key} className="flex min-h-0 flex-1 flex-col px-3.5 py-2">
            <SensorChart spec={spec} />
          </div>
        ))}
      </div>
    </Panel>
  );
}
