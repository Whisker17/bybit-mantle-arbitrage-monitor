import type { HTMLAttributes } from "react";

import { cn } from "@/lib/cn";

type BadgeVariant = "default" | "open" | "closed" | "muted" | "warning";

const variants: Record<BadgeVariant, string> = {
  default: "bg-muted text-muted-foreground border-border",
  open: "bg-positive/15 text-positive border-positive/30",
  closed: "bg-muted text-muted-foreground border-border",
  muted: "bg-muted/60 text-muted-foreground border-border/60",
  warning: "bg-warning/15 text-warning border-warning/40",
};

export function Badge({
  className,
  variant = "default",
  ...props
}: HTMLAttributes<HTMLSpanElement> & { variant?: BadgeVariant }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm border px-1.5 py-0.5 text-[10px] font-medium tracking-wide uppercase",
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}
