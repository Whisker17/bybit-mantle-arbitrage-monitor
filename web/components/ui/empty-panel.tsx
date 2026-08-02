import { cn } from "@/lib/cn";

/** Empty-state box shared by pair-detail panels.
 *
 * ``dashed`` is the WHI-759 default. WHI-769 MM panel uses ``solid`` so
 * missing-label states are not a content-free dashed placeholder.
 */
export function EmptyPanel({
  message,
  className,
  variant = "dashed",
}: {
  message: string;
  className?: string;
  variant?: "dashed" | "solid";
}) {
  return (
    <div
      className={cn(
        "rounded-md border border-border px-3 py-6 text-center text-[11px] text-muted-foreground",
        variant === "dashed" && "border-dashed",
        variant === "solid" && "bg-muted/20",
        className,
      )}
    >
      {message}
    </div>
  );
}
