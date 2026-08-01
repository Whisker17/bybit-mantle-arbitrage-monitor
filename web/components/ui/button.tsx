import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

type Variant = "default" | "ghost" | "outline";

const variants: Record<Variant, string> = {
  default:
    "bg-primary text-primary-foreground hover:bg-primary/90 border-transparent",
  ghost: "bg-transparent hover:bg-muted text-foreground border-transparent",
  outline:
    "bg-transparent border-border text-foreground hover:bg-muted/60",
};

export function Button({
  className,
  variant = "default",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      className={cn(
        "inline-flex h-8 items-center justify-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        "disabled:pointer-events-none disabled:opacity-50",
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}
