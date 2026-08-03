import { AlertTriangle } from "lucide-react";

import type { HealthResponse } from "@/lib/types";
import { fmtAgeMs } from "@/lib/format";

type Props = {
  health: HealthResponse | null;
  /** Hard failure fetching /api/health (or total API outage). */
  fetchError: string | null;
  /**
   * Soft failure on /api/pairs while health may still be fine — keep last rows
   * but never look silently live (WHI-758 outage requirement).
   */
  pairsError?: string | null;
};

/**
 * Explicit yellow/red bar when the feed is dead, journal is unreachable, or
 * the overview payload cannot refresh. Must never look like a healthy panel
 * with quietly stale numbers.
 */
export function StaleBanner({ health, fetchError, pairsError }: Props) {
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

  const reasons: string[] = [];
  if (health?.error) reasons.push(health.error);
  if (health && !health.db_exists) reasons.push("collector journal missing");
  if (health && !health.collector_alive) {
    // WHI-821: "feed down" = process/liveness, not per-symbol quote age.
    reasons.push(
      health.age_ms != null
        ? `feed down (age ${fmtAgeMs(health.age_ms)})`
        : "feed down (collector not alive)",
    );
  }
  if (pairsError) {
    reasons.push(`overview refresh failed: ${pairsError}`);
  }

  const healthBad = health != null && !health.ok;
  if (!healthBad && !pairsError && reasons.length === 0) return null;
  if (health == null && !pairsError) return null;

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
          {health?.gap_recent ? " · recent collector gap recorded" : ""}
        </div>
      </div>
    </div>
  );
}
