import { cn } from "@/lib/cn";

/** Dashed empty-state box shared by pair-detail panels. */
export function EmptyPanel({
  message,
  className,
}: {
  message: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-md border border-dashed border-border px-3 py-6 text-center text-[11px] text-muted-foreground",
        className,
      )}
    >
      {message}
    </div>
  );
}
