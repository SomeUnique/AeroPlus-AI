import { cn } from "@/lib/cn";
import { Label } from "./Label";
import type { Tone } from "./StatusBadge";

export function Stat({
  label,
  value,
  unit,
  tone = "accent",
  hint,
  className,
  valueClassName,
}: {
  label: string;
  value: React.ReactNode;
  unit?: string;
  tone?: Tone;
  hint?: React.ReactNode;
  className?: string;
  valueClassName?: string;
}) {
  const toneText: Record<Tone, string> = {
    nominal: "text-nominal",
    warning: "text-warning",
    critical: "text-critical",
    accent: "text-ink",
    muted: "text-muted",
  };

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label>{label}</Label>
      <div className="flex items-baseline gap-1.5">
        <span
          className={cn(
            "font-mono text-xl leading-none tracking-tight",
            toneText[tone],
            valueClassName,
          )}
        >
          {value}
        </span>
        {unit && (
          <span className="font-mono text-[11px] text-faint">{unit}</span>
        )}
      </div>
      {hint && <div className="text-[10px] text-faint">{hint}</div>}
    </div>
  );
}
