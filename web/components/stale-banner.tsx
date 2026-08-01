import { AlertTriangle } from "lucide-react";

import type { HealthResponse } from "@/lib/types";
import { fmtAgeMs } from "@/lib/format";

type Props = {
  health: HealthResponse | null;
  fetchError: string | null;
};

/**
 * Explicit yellow bar when the feed is dead or the journal is unreachable.
 * Must never look like a healthy panel with quietly stale numbers.
 */
export function StaleBanner({ health, fetchError }: Props) {
  if (fetchError) {
    return (
      <div
        role="alert"
        className="mb-3 flex items-start gap-2 rounded-md border border-destructive/50 bg-destructive/15 px-3 py-2 text-xs text-destructive-foreground"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
        <div>
          <div className="font-semibold tracking-wide uppercase text-[11px]">
            API unreachable
          </div>
          <div className="opacity-90">{fetchError}</div>
        </div>
      </div>
    );
  }

  if (!health) return null;

  const reasons: string[] = [];
  if (health.error) reasons.push(health.error);
  if (!health.db_exists) reasons.push("collector journal missing");
  if (!health.collector_alive) {
    reasons.push(
      health.age_ms != null
        ? `collector data stale (age ${fmtAgeMs(health.age_ms)})`
        : "collector not alive",
    );
  }

  if (health.ok && reasons.length === 0) return null;

  return (
    <div
      role="alert"
      className="mb-3 flex items-start gap-2 rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <div>
        <div className="font-semibold tracking-wide uppercase text-[11px] text-warning">
          Data feed warning — numbers may be frozen
        </div>
        <div className="text-warning/90">
          {reasons.length > 0
            ? reasons.join(" · ")
            : "collector health degraded"}
          {health.gap_recent ? " · recent collector gap recorded" : ""}
        </div>
      </div>
    </div>
  );
}
