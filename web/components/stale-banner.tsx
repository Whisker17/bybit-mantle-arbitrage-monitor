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

function feedReasons(health: HealthResponse): string[] {
  const reasons: string[] = [];
  if (health.error) reasons.push(health.error);
  if (!health.db_exists) reasons.push("collector journal missing");

  const state = health.feed_state;
  if (state === "feed_down" || (!state && !health.collector_alive)) {
    // WHI-821 / WHI-825: feed down = process/liveness, not per-symbol quote age.
    reasons.push(
      health.age_ms != null
        ? `feed down (age ${fmtAgeMs(health.age_ms)})`
        : "feed down (collector not alive)",
    );
  } else if (state === "feed_quiet") {
    reasons.push(
      health.age_ms != null
        ? `feed quiet (data age ${fmtAgeMs(health.age_ms)}; process alive)`
        : "feed quiet (process alive, tick feeds silent)",
    );
  } else if (state === "gap" || health.collector_down_gap_recent) {
    reasons.push("known collector downtime gap recorded");
  } else if (health.gap_recent) {
    reasons.push("recent collector gap recorded");
  }

  if (health.recovery_hint) {
    reasons.push(health.recovery_hint);
  }
  return reasons;
}

/**
 * Explicit yellow/red bar when the feed is dead, quiet, gapped, journal is
 * unreachable, or the overview payload cannot refresh. Must never look like a
 * healthy panel with quietly stale numbers.
 *
 * WHI-825 three-state vocabulary: feed_down / feed_quiet / gap (+ ok).
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
  if (health) {
    reasons.push(...feedReasons(health));
  }
  if (pairsError) {
    reasons.push(`overview refresh failed: ${pairsError}`);
  }

  // Banner only for hard/actionable states. feed_quiet is status-bar only
  // (closed-session silence is normal — WHI-821 / WHI-825 same family).
  const feedBad =
    health != null &&
    (health.feed_state === "feed_down" ||
      health.feed_state === "gap" ||
      !health.ok ||
      Boolean(health.collector_down_gap_recent));
  if (!feedBad && !pairsError) return null;
  if (health == null && !pairsError) return null;

  // Drop soft quiet reasons from the banner body when we only showed them
  // because of pairsError / other hard states.
  const hardReasons = reasons.filter(
    (r) => !r.startsWith("feed quiet"),
  );

  const title =
    health?.feed_state === "feed_down"
      ? "Feed down — collector not writing"
      : health?.feed_state === "gap"
        ? "Collector downtime gap — numbers skip this hole"
        : "Data feed warning — numbers may be frozen";

  return (
    <div
      role="alert"
      className="mb-3 flex items-start gap-2 rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <div>
        <div className="font-semibold tracking-wide uppercase text-[11px] text-warning">
          {title}
        </div>
        <div className="text-warning/90">
          {hardReasons.length > 0
            ? hardReasons.join(" · ")
            : reasons.length > 0
              ? reasons.join(" · ")
              : "collector health degraded"}
        </div>
      </div>
    </div>
  );
}
