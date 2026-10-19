import { cn } from "@/lib/cn";

export function Panel({
  className,
  glow = false,
  children,
  ...rest
}: React.HTMLAttributes<HTMLDivElement> & { glow?: boolean }) {
  return (
    <div
      className={cn("panel", glow && "panel-top-glow", className)}
      {...rest}
    >
      {children}
    </div>
  );
}
