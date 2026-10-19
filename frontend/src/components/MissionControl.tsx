"use client";

import { useState } from "react";
import { motion, type Variants } from "framer-motion";

import { useTelemetry } from "@/lib/useTelemetry";
import { usePrefersReducedMotion } from "@/lib/useReducedMotion";
import { useAlert } from "@/lib/useAlert";
import { AgentConsole } from "./AgentConsole";
import { BootSequence } from "./BootSequence";
import { CommsPanel } from "./CommsPanel";
import { ConnectionBanner } from "./ConnectionBanner";
import { DemoControls } from "./DemoControls";
import { EarthView } from "./EarthView";
import { SensorPanel } from "./SensorPanel";
import { SubsystemGrid } from "./SubsystemGrid";
import { TopBar } from "./TopBar";

const EASE = "easeOut";

export function MissionControl() {
  useTelemetry();
  const reduced = usePrefersReducedMotion();
  const alert = useAlert();
  const [booted, setBooted] = useState(false);

  const container: Variants = {
    hidden: {},
    show: {
      transition: {
        staggerChildren: reduced ? 0 : 0.07,
        delayChildren: reduced ? 0 : 0.04,
      },
    },
  };

  const item: Variants = reduced
    ? { hidden: { opacity: 1 }, show: { opacity: 1 } }
    : {
        hidden: { opacity: 0, y: 14 },
        show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: EASE } },
      };

  return (
    <div
      className="relative flex h-dvh w-full flex-col overflow-hidden px-3 pb-3"
      data-alert={alert.level.toLowerCase()}
    >
      <ConnectionBanner />
      <TopBar alert={alert} />

      <motion.main
        variants={container}
        initial="hidden"
        animate={booted ? "show" : "hidden"}
        className="grid min-h-0 flex-1 gap-3"
        style={{
          gridTemplateColumns:
            "minmax(320px, 21vw) minmax(0, 1fr) minmax(320px, 23vw)",
          gridTemplateRows: "minmax(0, 1fr) minmax(210px, 28%)",
        }}
      >
        <motion.div
          variants={item}
          className="row-span-2 min-h-0 [&>*]:h-full"
        >
          <SensorPanel />
        </motion.div>
        <motion.div variants={item} className="min-h-0 [&>*]:h-full">
          <EarthView level={alert.level} />
        </motion.div>
        <motion.div variants={item} className="min-h-0 [&>*]:h-full">
          <SubsystemGrid alert={alert} />
        </motion.div>
        <motion.div
          variants={item}
          className="col-start-2 min-h-0 [&>*]:h-full"
        >
          <AgentConsole />
        </motion.div>
        <motion.div
          variants={item}
          className="col-start-3 min-h-0 [&>*]:h-full"
        >
          <CommsPanel />
        </motion.div>
      </motion.main>

      <DemoControls />

      {!booted && (
        <BootSequence reduced={reduced} onComplete={() => setBooted(true)} />
      )}
    </div>
  );
}
