"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, Loader2 } from "lucide-react";

import { MISSION } from "@/lib/config";
import { SUBSYSTEM_ORDER } from "@/lib/sensors";

const FULL_MS = 2600;
const REDUCED_MS = 500;

export function BootSequence({
  onComplete,
  reduced,
}: {
  onComplete: () => void;
  reduced: boolean;
}) {
  const [visible, setVisible] = useState(true);
  const [stage, setStage] = useState(0);
  const [checked, setChecked] = useState(0);
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;

  useEffect(() => {
    if (reduced) {
      setStage(2);
      setChecked(SUBSYSTEM_ORDER.length);
      const t = setTimeout(() => {
        setVisible(false);
        onCompleteRef.current();
      }, REDUCED_MS);
      return () => clearTimeout(t);
    }

    const timers: ReturnType<typeof setTimeout>[] = [];
    timers.push(setTimeout(() => setStage(1), 520));
    timers.push(setTimeout(() => setStage(2), 1050));
    SUBSYSTEM_ORDER.forEach((_, i) => {
      timers.push(setTimeout(() => setChecked(i + 1), 1300 + i * 130));
    });
    timers.push(
      setTimeout(() => {
        setVisible(false);
        onCompleteRef.current();
      }, FULL_MS),
    );
    return () => timers.forEach(clearTimeout);
  }, [reduced]);

  const letters = useMemo(() => "AEROPULSE".split(""), []);

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center bg-base"
          initial={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: reduced ? 0.1 : 0.55, ease: "easeInOut" }}
        >
          <div className="pointer-events-none absolute inset-0 app-grid" />
          <div className="relative flex w-[420px] flex-col items-center">
            <motion.div
              className="flex items-baseline gap-2"
              initial={{ opacity: 0, y: 10, letterSpacing: "0.6em" }}
              animate={{ opacity: 1, y: 0, letterSpacing: "0.22em" }}
              transition={{ duration: reduced ? 0.1 : 0.9, ease: "easeOut" }}
            >
              <span className="text-glow text-2xl font-semibold tracking-[0.22em] text-ink">
                {letters.join("")}
              </span>
              <span className="font-mono text-sm text-accent">AI</span>
            </motion.div>

            <motion.div
              className="mt-2 font-mono text-[10px] uppercase tracking-[0.42em] text-muted"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: reduced ? 0 : 0.35, duration: 0.6 }}
            >
              MISSION CONTROL
            </motion.div>

            <motion.div
              className="mt-6 h-px w-full bg-hairline"
              initial={{ scaleX: 0 }}
              animate={{ scaleX: 1 }}
              transition={{ delay: reduced ? 0 : 0.3, duration: reduced ? 0.1 : 1.4 }}
            />

            <div className="mt-5 flex h-4 items-center gap-2 font-mono text-[11px] text-muted">
              <AnimatePresence mode="wait">
                {stage === 0 && (
                  <motion.span
                    key="idle"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                  >
                    INITIALISING&nbsp; AVIONICS BUS
                  </motion.span>
                )}
                {stage === 1 && (
                  <motion.span
                    key="uplink"
                    className="flex items-center gap-2 text-accent"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                  >
                    <Loader2 className="size-3 animate-spin" />
                    ESTABLISHING UPLINK
                  </motion.span>
                )}
                {stage === 2 && (
                  <motion.span
                    key="checks"
                    className="text-muted"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                  >
                    VERIFYING SUBSYSTEMS
                  </motion.span>
                )}
              </AnimatePresence>
            </div>

            <ul className="mt-4 flex w-full flex-col gap-1.5">
              {SUBSYSTEM_ORDER.map((subsystem, i) => {
                const done = i < checked;
                return (
                  <li
                    key={subsystem}
                    className="flex items-center justify-between font-mono text-[11px]"
                  >
                    <span className={done ? "text-muted" : "text-faint"}>
                      {subsystem.toUpperCase()}
                    </span>
                    <span
                      className={done ? "text-nominal" : "text-faint/60"}
                      aria-live="polite"
                    >
                      {done ? (
                        <span className="flex items-center gap-1.5">
                          <span className="text-[10px] tracking-widest">NOMINAL</span>
                          <Check className="size-3" />
                        </span>
                      ) : (
                        <span className="text-[10px] tracking-widest opacity-60">
                          STANDBY
                        </span>
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>

            <div className="mt-6 font-mono text-[9px] uppercase tracking-[0.3em] text-faint/70">
              {MISSION.vehicle} / {MISSION.regime}
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
