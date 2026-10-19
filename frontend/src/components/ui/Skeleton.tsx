import { cn } from "@/lib/cn";

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn("shimmer rounded-md bg-white/[0.03]", className)}
      aria-hidden
    />
  );
}

export function EmptyState({
  icon,
  title,
  detail,
  className,
}: {
  icon?: React.ReactNode;
  title: string;
  detail?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex h-full flex-col items-center justify-center gap-2 text-center",
        className,
      )}
    >
      {icon && <div className="text-faint/70">{icon}</div>}
      <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-muted">
        {title}
      </div>
      {detail && <div className="max-w-[26ch] text-[10px] text-faint">{detail}</div>}
    </div>
  );
}
