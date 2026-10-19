import type { CommandStatus } from "./ws";

export interface UplinkCommandOption {
  name: string;
  label: string;
  detail: string;
}

/** Mirrors backend `comms/commands.py` UPLINK_COMMANDS. Operator-facing only. */
export const UPLINK_COMMAND_OPTIONS: UplinkCommandOption[] = [
  {
    name: "SWITCH_TO_SECONDARY_ARRAY",
    label: "Backup Array",
    detail: "Route the power bus through the secondary solar array.",
  },
  {
    name: "THROTTLE_PAYLOAD",
    label: "Throttle",
    detail: "Reduce payload draw to protect the power budget.",
  },
  {
    name: "ENTER_SAFE_MODE",
    label: "Safe Mode",
    detail: "Power down non-essential loads and hold attitude.",
  },
];

const BY_NAME = new Map(UPLINK_COMMAND_OPTIONS.map((c) => [c.name, c]));

export function commandLabel(name: string): string {
  return BY_NAME.get(name)?.label ?? name;
}

/** Plain-language text for each lifecycle stage (human-in-the-loop). */
export const COMMAND_STAGE_LABEL: Record<CommandStatus, string> = {
  PENDING_APPROVAL: "Awaiting approval",
  APPROVED: "Approved",
  QUEUED_FOR_PASS: "Queued for pass",
  UPLINKED: "Uplinked",
  EXECUTED: "Executed",
};
