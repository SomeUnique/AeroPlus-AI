"use client";

import { useState } from "react";
import { Check, CircleSlash, Upload, X } from "lucide-react";

import { cn } from "@/lib/cn";
import {
  COMMAND_STAGE_LABEL,
  UPLINK_COMMAND_OPTIONS,
  commandLabel,
} from "@/lib/commands";
import { approveCommand, rejectCommand, submitCommand } from "@/lib/comms";
import { useTelemetryStore } from "@/lib/store";
import type { CommandStatus } from "@/lib/ws";
import { Label } from "./ui/Label";
import { StatusDot, type Tone } from "./ui/StatusBadge";

const STATUS_TONE: Record<CommandStatus, Tone> = {
  PENDING_APPROVAL: "warning",
  APPROVED: "accent",
  QUEUED_FOR_PASS: "accent",
  UPLINKED: "accent",
  EXECUTED: "nominal",
};

const STAGE_CLASS: Record<Tone, string> = {
  warning: "text-warning",
  accent: "text-accent",
  nominal: "text-nominal",
  critical: "text-critical",
  muted: "text-muted",
};

export function UplinkQueue() {
  const commands = useTelemetryStore((s) => s.commands);
  const [error, setError] = useState<string | null>(null);

  const pending = commands.filter((c) => c.status === "PENDING_APPROVAL").length;
  const ordered = [...commands].reverse();

  const fire = (ok: boolean) => {
    setError(ok ? null : "Link offline — reconnect to uplink commands.");
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-2 px-3.5 pb-1.5 pt-2.5">
        <div className="flex items-center gap-2">
          <Upload className="size-3.5 text-accent" />
          <Label className="text-muted">Uplink Queue</Label>
          {pending > 0 && (
            <span className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-warning">
              {pending} to approve
            </span>
          )}
        </div>
        <span className="font-mono text-[9px] uppercase tracking-wider text-faint">
          Human-in-the-loop
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5 px-3.5 pb-2">
        {UPLINK_COMMAND_OPTIONS.map((opt) => (
          <button
            key={opt.name}
            type="button"
            title={opt.detail}
            onClick={() => fire(submitCommand(opt.name))}
            className="rounded border border-hairline-2 bg-white/[0.02] px-2 py-1 font-mono text-[10px] text-muted transition-colors hover:border-accent/40 hover:text-ink"
          >
            + {opt.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="px-3.5 pb-1.5 font-mono text-[9px] text-warning">{error}</div>
      )}

      <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto px-3.5 pb-3">
        {ordered.length === 0 ? (
          <div className="flex flex-1 items-center justify-center text-center font-mono text-[10px] text-faint">
            No commands queued
          </div>
        ) : (
          ordered.map((cmd) => {
            const tone = STATUS_TONE[cmd.status];
            return (
              <div
                key={cmd.command_id}
                className="rounded-md border border-hairline/80 bg-white/[0.015] px-2.5 py-1.5"
              >
                <div className="flex items-center gap-2">
                  <StatusDot tone={tone} animate={cmd.status === "PENDING_APPROVAL"} />
                  <span className="truncate font-mono text-[11px] text-ink">
                    {commandLabel(cmd.command)}
                  </span>
                  <span
                    className={cn(
                      "ml-auto shrink-0 font-mono text-[9px] uppercase tracking-wider",
                      STAGE_CLASS[tone],
                    )}
                  >
                    {COMMAND_STAGE_LABEL[cmd.status]}
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-1.5 truncate font-mono text-[9px] text-faint">
                  <CircleSlash className="size-2.5 shrink-0" />
                  <span className="truncate">{cmd.detail}</span>
                </div>
                {cmd.status === "PENDING_APPROVAL" && (
                  <div className="mt-1.5 flex gap-1.5">
                    <button
                      type="button"
                      onClick={() => fire(approveCommand(cmd.command_id))}
                      className="flex flex-1 items-center justify-center gap-1 rounded border border-nominal/30 bg-nominal/10 px-2 py-1 font-mono text-[9px] uppercase tracking-wider text-nominal transition-colors hover:bg-nominal/20"
                    >
                      <Check className="size-3" />
                      Approve
                    </button>
                    <button
                      type="button"
                      onClick={() => fire(rejectCommand(cmd.command_id))}
                      className="flex flex-1 items-center justify-center gap-1 rounded border border-hairline-2 bg-white/[0.02] px-2 py-1 font-mono text-[9px] uppercase tracking-wider text-muted transition-colors hover:border-critical/40 hover:text-critical"
                    >
                      <X className="size-3" />
                      Reject
                    </button>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
