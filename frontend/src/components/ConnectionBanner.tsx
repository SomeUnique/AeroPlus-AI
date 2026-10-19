"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Loader2, WifiOff } from "lucide-react";

import { useTelemetryStore } from "@/lib/store";
import { fmtAge } from "@/lib/format";

export function ConnectionBanner() {
  const connection = useTelemetryStore((s) => s.connection);
  const attempt = useTelemetryStore((s) => s.attempt);
  const lastMessageAt = useTelemetryStore((s) => s.lastMessageAt);
  const health = useTelemetryStore((s) => s.health);

  const hidden = connection === "live";
  // Derived only from store state so the server and first client render agree.
  // `navigator.onLine` and wall-clock ages are not hydration-safe.
  const offline = connection === "offline";
  const age = lastMessageAt > 0 ? fmtAge(Date.now() - lastMessageAt) : "--";

  return (
    <AnimatePresence>
      {!hidden && (
        <motion.div
          className="pointer-events-none fixed inset-x-0 top-0 z-40 flex justify-center pt-2"
          initial={{ y: -40, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: -40, opacity: 0 }}
          transition={{ duration: 0.3, ease: "easeOut" }}
        >
          <div className="pointer-events-auto flex items-center gap-3 rounded-lg border border-warning/30 bg-[#1a1407]/90 px-4 py-2 backdrop-blur-md">
            {offline ? (
              <WifiOff className="size-3.5 text-warning" />
            ) : (
              <Loader2 className="size-3.5 animate-spin text-warning" />
            )}
            <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-warning">
              {offline ? "Uplink offline" : "Backend disconnected - reconnecting"}
            </span>
            <span className="h-3 w-px bg-warning/30" />
            <span className="font-mono text-[10px] text-muted">
              attempt {attempt} · last {age}
            </span>
            {health && (
              <span className="font-mono text-[10px] text-faint">
                api/{health.version}
              </span>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
