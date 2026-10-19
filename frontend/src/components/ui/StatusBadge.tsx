import { cn } from "@/lib/cn";
import type { Severity, SystemStatus } from "@/lib/ws";

export type Tone = "nominal" | "warning" | "critical" | "accent" | "muted";

const TONE_DOT: Record<Tone, string> = {
  nominal: "bg-nominal text-nominal",
  warning: "bg-warning text-warning",
  critical: "bg-critical text-critical",
  accent: "bg-accent text-accent",
  muted: "bg-faint text-faint",
};

const TONE_TEXT: Record<Tone, string> = {
  nominal: "text-nominal",
  warning: "text-warning",
  critical: "text-critical",
  accent: "text-accent",
  muted: "text-muted",
};

const TONE_RING: Record<Tone, string> = {
  nominal: "border-nominal/30 bg-nominal/10",
  warning: "border-warning/30 bg-warning/10",
  critical: "border-critical/30 bg-critical/10",
  accent: "border-accent/30 bg-accent/10",
  muted: "border-hairline-2 bg-white/[0.02]",
};

export function toneForStatus(status: SystemStatus): Tone {
  if (status === "CRITICAL") return "critical";
  if (status === "WARNING") return "warning";
  return "nominal";
}

export function toneForSeverity(severity: Severity): Tone {
  if (severity === "critical") return "critical";
  if (severity === "warning") return "warning";
  return "accent";
}

export function StatusDot({
  tone,
  className,
  animate = false,
}: {
  tone: Tone;
  className?: string;
  animate?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-block size-1.5 rounded-full",
        TONE_DOT[tone],
        animate && "pulse-dot",
        className,
      )}
    />
  );
}

export function StatusBadge({
  tone,
  children,
  className,
  dot = true,
  animateDot = false,
}: {
  tone: Tone;
  children: React.ReactNode;
  className?: string;
  dot?: boolean;
  animateDot?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[10px] font-medium uppercase tracking-[0.14em]",
        TONE_RING[tone],
        TONE_TEXT[tone],
        className,
      )}
    >
      {dot && <StatusDot tone={tone} animate={animateDot} />}
      {children}
    </span>
  );
}
