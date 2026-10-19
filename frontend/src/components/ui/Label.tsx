import { cn } from "@/lib/cn";

export function Label({
  children,
  className,
  as: Tag = "span",
}: {
  children: React.ReactNode;
  className?: string;
  as?: "span" | "div" | "p";
}) {
  return (
    <Tag
      className={cn(
        "text-[10px] font-medium uppercase leading-none tracking-[0.2em] text-muted",
        className,
      )}
    >
      {children}
    </Tag>
  );
}
