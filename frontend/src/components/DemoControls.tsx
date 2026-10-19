"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ChevronRight,
  FlaskConical,
  RotateCcw,
  TriangleAlert,
  X,
} from "lucide-react";

import { FAULT_OPTIONS, injectFault, resetScenario } from "@/lib/faults";
import { useTelemetryStore } from "@/lib/store";
import { Label } from "./ui/Label";
import { Panel } from "./ui/Panel";

const EMPTY_FAULTS: string[] = [];

export function DemoControls() {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const activeFaults = useTelemetryStore(
    (s) => s.health?.active_faults ?? EMPTY_FAULTS,
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (
        e.metaKey ||
        e.ctrlKey ||
        e.altKey ||
        (target &&
          (target.tagName === "INPUT" ||
            target.tagName === "TEXTAREA" ||
            target.isContentEditable))
      ) {
        return;
      }
      if (e.key === "d" || e.key === "D") {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const run = async (type: string, fn: () => Promise<boolean>) => {
    setBusy(type);
    await fn();
    setBusy(null);
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="fixed bottom-4 right-4 z-40 flex items-center gap-2 rounded-md border border-hairline-2 bg-surface/80 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.16em] text-muted backdrop-blur transition-colors hover:border-accent/40 hover:text-ink"
      >
        <FlaskConical className="size-3.5 text-accent" />
        Demo Controls
        <kbd className="rounded border border-hairline-2 px-1 text-[9px] text-faint">
          D
        </kbd>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ x: 360, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: 360, opacity: 0 }}
            transition={{ type: "spring", stiffness: 320, damping: 34 }}
            className="fixed bottom-16 right-4 z-40 w-[320px]"
          >
            <Panel glow className="flex flex-col gap-3 p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <FlaskConical className="size-3.5 text-accent" />
                  <Label className="text-muted">Demo Controls</Label>
                </div>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="text-faint transition-colors hover:text-ink"
                  aria-label="Close demo controls"
                >
                  <X className="size-3.5" />
                </button>
              </div>

              <div className="hairline-h" />

              <div className="flex items-center gap-2">
                <TriangleAlert className="size-3 text-faint" />
                <span className="font-mono text-[10px] text-faint">
                  ACTIVE FAULTS
                </span>
                <span className="ml-auto flex flex-wrap gap-1">
                  {activeFaults.length === 0 ? (
                    <span className="font-mono text-[10px] text-muted">none</span>
                  ) : (
                    activeFaults.map((f) => (
                      <span
                        key={f}
                        className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-warning"
                      >
                        {f}
                      </span>
                    ))
                  )}
                </span>
              </div>

              <div className="flex flex-col gap-2">
                {FAULT_OPTIONS.map((fault) => (
                  <button
                    key={fault.type}
                    type="button"
                    disabled={busy !== null}
                    onClick={() => run(fault.type, () => injectFault(fault.type))}
                    className="group flex items-center gap-3 rounded-md border border-hairline bg-white/[0.015] px-3 py-2 text-left transition-colors hover:border-warning/40 hover:bg-warning/[0.04] disabled:opacity-50"
                  >
                    <ChevronRight className="size-3.5 shrink-0 text-warning" />
                    <span className="flex min-w-0 flex-col">
                      <span className="text-[12px] text-ink">
                        {fault.label}
                        <span className="ml-2 font-mono text-[9px] uppercase tracking-wider text-faint">
                          {fault.subsystem}
                        </span>
                      </span>
                      <span className="truncate text-[10px] text-faint">
                        {fault.detail}
                      </span>
                    </span>
                    {busy === fault.type && (
                      <span className="ml-auto font-mono text-[9px] text-warning">
                        ...
                      </span>
                    )}
                  </button>
                ))}
              </div>

              <div className="hairline-h" />

              <button
                type="button"
                disabled={busy !== null}
                onClick={() => run("reset", resetScenario)}
                className="flex items-center justify-center gap-2 rounded-md border border-hairline-2 bg-white/[0.02] px-3 py-2 font-mono text-[10px] uppercase tracking-[0.16em] text-muted transition-colors hover:border-accent/40 hover:text-ink disabled:opacity-50"
              >
                <RotateCcw
                  className={`size-3.5 ${busy === "reset" ? "animate-spin" : ""}`}
                />
                Reset Scenario
              </button>
            </Panel>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
