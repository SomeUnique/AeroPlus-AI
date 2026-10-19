"use client";

import { useEffect, useRef } from "react";
import { motion } from "framer-motion";
import {
  BrainCircuit,
  Check,
  ChevronRight,
  CircleDot,
  FileText,
  Loader2,
  TriangleAlert,
} from "lucide-react";

import { useTelemetryStore } from "@/lib/store";
import type { ActionStatus, AgentPhase, Severity } from "@/lib/ws";
import { Label } from "./ui/Label";
import { Panel } from "./ui/Panel";
import { EmptyState } from "./ui/Skeleton";

const PHASE_TONE: Record<AgentPhase, string> = {
  observe: "text-accent",
  reason: "text-muted",
  plan: "text-warning",
  verify: "text-nominal",
};

const STATUS_META: Record<
  ActionStatus,
  { tone: string; label: string; Icon: typeof Check; spin?: boolean }
> = {
  proposed: {
    tone: "text-accent border-accent/30 bg-accent/10",
    label: "PROPOSED",
    Icon: CircleDot,
  },
  executing: {
    tone: "text-warning border-warning/30 bg-warning/10",
    label: "EXECUTING",
    Icon: Loader2,
  },
  succeeded: {
    tone: "text-nominal border-nominal/30 bg-nominal/10",
    label: "DONE",
    Icon: Check,
  },
  failed: {
    tone: "text-critical border-critical/30 bg-critical/10",
    label: "FAILED",
    Icon: TriangleAlert,
  },
};

const SEV_TONE: Record<Severity, string> = {
  info: "text-accent",
  warning: "text-warning",
  critical: "text-critical",
};

function Counter({ label, value }: { label: string; value: number }) {
  return (
    <span className="flex items-baseline gap-1.5 font-mono text-[10px] text-faint">
      <span className="uppercase tracking-[0.14em]">{label}</span>
      <span className="text-muted">{value}</span>
    </span>
  );
}

export function AgentConsole() {
  const thinking = useTelemetryStore((s) => s.thinking);
  const actions = useTelemetryStore((s) => s.actions);
  const reports = useTelemetryStore((s) => s.reports);
  const counts = useTelemetryStore((s) => s.counts);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thinking.length]);

  const latestReport = reports[0];

  return (
    <Panel glow className="flex min-h-0 flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 pb-2 pt-3">
        <div className="flex items-center gap-2">
          <BrainCircuit className="size-3.5 text-accent" />
          <Label className="text-muted">Agent Console</Label>
          <span className="rounded border border-hairline-2 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-widest text-faint">
            Decide / Act
          </span>
        </div>
        <div className="flex items-center gap-4">
          <Counter label="Anomalies" value={counts.anomalies} />
          <Counter label="Steps" value={thinking.length} />
          <Counter label="Actions" value={counts.actions} />
          <Counter label="Reports" value={counts.reports} />
        </div>
      </div>

      <div className="hairline-h mx-4" />

      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[1.5fr_1fr]">
        {/* reasoning trace */}
        <div className="flex min-h-0 flex-col border-hairline/70 md:border-r">
          <div className="px-4 pt-2.5">
            <Label className="text-faint">Reasoning Trace</Label>
          </div>
          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
            {thinking.length === 0 ? (
              <EmptyState
                icon={<BrainCircuit className="size-5" />}
                title="No active incidents"
                detail="All systems nominal. Monitoring the telemetry stream."
              />
            ) : (
              <div className="flex flex-col gap-1.5">
                {thinking.map((step, index) => (
                  <motion.div
                    key={`${step.step}-${index}`}
                    initial={{ opacity: 0, x: -6 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.25 }}
                    className="flex items-start gap-2.5"
                  >
                    <span className="mt-0.5 w-5 shrink-0 text-right font-mono text-[10px] text-faint">
                      {step.step}
                    </span>
                    <span
                      className={`mt-0.5 w-14 shrink-0 font-mono text-[9px] uppercase tracking-[0.14em] ${PHASE_TONE[step.phase]}`}
                    >
                      {step.phase}
                    </span>
                    <span className="text-[11px] leading-snug text-ink/90">
                      {step.content}
                    </span>
                  </motion.div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="flex min-h-0 flex-col">
          <div className="px-4 pt-2.5">
            <Label className="text-faint">Actions</Label>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
            {actions.length === 0 ? (
              <div className="font-mono text-[11px] text-faint">
                No actions issued.
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                {actions.map((action) => {
                  const meta = STATUS_META[action.status];
                  return (
                    <motion.div
                      key={action.action_id}
                      initial={{ opacity: 0, x: 8 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ duration: 0.25 }}
                      className="rounded-md border border-hairline/80 bg-white/[0.015] p-2.5"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate font-mono text-[11px] text-ink">
                          {action.action}
                        </span>
                        <span
                          className={`flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-widest ${meta.tone}`}
                        >
                          <meta.Icon
                            className={`size-2.5 ${
                              action.status === "executing" ? "animate-spin" : ""
                            }`}
                          />
                          {meta.label}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center gap-1.5 font-mono text-[10px] text-faint">
                        <ChevronRight className="size-3 shrink-0" />
                        <span className="truncate">{action.target}</span>
                      </div>
                      <p className="mt-1 text-[10px] leading-snug text-muted">
                        {action.rationale}
                      </p>
                    </motion.div>
                  );
                })}
              </div>
            )}
          </div>

          {latestReport && (
            <div className="border-t border-hairline/70 px-4 py-2.5">
              <div className="flex items-center gap-2">
                <FileText className="size-3.5 shrink-0 text-accent" />
                <span className="truncate text-[11px] text-ink">
                  {latestReport.title}
                </span>
                <span
                  className={`ml-auto font-mono text-[9px] uppercase tracking-widest ${SEV_TONE[latestReport.severity]}`}
                >
                  report
                </span>
              </div>
              <p className="mt-1 line-clamp-2 text-[10px] text-faint">
                {latestReport.summary}
              </p>
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}
