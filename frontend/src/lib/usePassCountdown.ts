"use client";

import { useEffect, useRef, useState } from "react";

import { useTelemetryStore } from "./store";

export interface PassCountdown {
  /** Which milestone the countdown targets, or null when none is known. */
  milestone: "aos" | "los" | null;
  /** Live sim-seconds remaining, extrapolated between backend refreshes. */
  seconds: number | null;
  /** True while the SIMULATED RF link is CONNECTED (inside a pass). */
  inPass: boolean;
}

interface Anchor {
  milestone: "aos" | "los";
  simRemaining: number;
  wallAt: number;
  scale: number;
}

const TICK_MS = 250;

/**
 * Counts down to AOS or LOS in *simulated* mission seconds.
 *
 * The backend only re-emits the pass schedule every 30 sim-seconds (and on
 * AOS/LOS), so we anchor each update to the wall clock and extrapolate by the
 * demo time scale so the display stays smooth between frames. This is a purely
 * cosmetic interpolation of SIMULATED values - it invents no data.
 */
export function usePassCountdown(): PassCountdown {
  const passInfo = useTelemetryStore((s) => s.passInfo);
  const linkState = useTelemetryStore((s) => s.linkStatus?.link_state ?? null);
  const anchor = useRef<Anchor | null>(null);
  const [state, setState] = useState<PassCountdown>({
    milestone: null,
    seconds: null,
    inPass: false,
  });

  useEffect(() => {
    const wallAt = Date.now();
    if (!passInfo) {
      anchor.current = null;
      return;
    }
    if (passInfo.event === "aos" && passInfo.window) {
      anchor.current = {
        milestone: "los",
        simRemaining: passInfo.window.duration_s,
        wallAt,
        scale: passInfo.time_scale,
      };
    } else if (passInfo.event === "next_pass" && passInfo.seconds_to_aos != null) {
      anchor.current = {
        milestone: "aos",
        simRemaining: passInfo.seconds_to_aos,
        wallAt,
        scale: passInfo.time_scale,
      };
    } else if (passInfo.event === "los") {
      anchor.current = null;
    }
  }, [passInfo]);

  useEffect(() => {
    const compute = () => {
      const a = anchor.current;
      const inPass = linkState === "CONNECTED";
      if (!a) {
        setState({ milestone: null, seconds: null, inPass });
        return;
      }
      const elapsedWall = (Date.now() - a.wallAt) / 1000;
      const remaining = Math.max(0, a.simRemaining - elapsedWall * a.scale);
      setState({ milestone: a.milestone, seconds: remaining, inPass });
    };
    compute();
    const id = setInterval(compute, TICK_MS);
    return () => clearInterval(id);
  }, [linkState]);

  return state;
}
