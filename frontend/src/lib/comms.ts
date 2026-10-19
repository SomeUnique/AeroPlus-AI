import { sendClientFrame } from "./useTelemetry";
import type { ClientFrame } from "./ws";

/**
 * Human-in-the-loop uplink actions. The agent never sends these — only an
 * operator does, and the server refuses a command that skips approval.
 * All frames travel over the shared /ws socket (mirrors backend
 * `UplinkCommandRequest`).
 */

function send(frame: ClientFrame): boolean {
  return sendClientFrame(frame);
}

export function submitCommand(command: string): boolean {
  return send({ type: "command_request", action: "submit", command });
}

export function approveCommand(commandId: string): boolean {
  return send({ type: "command_request", action: "approve", command_id: commandId });
}

export function rejectCommand(commandId: string): boolean {
  return send({ type: "command_request", action: "reject", command_id: commandId });
}
