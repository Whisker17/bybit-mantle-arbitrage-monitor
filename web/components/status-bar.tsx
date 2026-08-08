import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { fmtAgeMs, fmtReferenceSize, fmtSession, fmtTsMs } from "@/lib/format";
import type { HealthResponse, OverviewResponse } from "@/lib/types";

type Props = {
  health: HealthResponse | null;
  overview: OverviewResponse | null;
  pollMs: number;
  rowCount: number;
  filteredCount: number;
  /** Market display name for the status strip (WHI-774). */
  displayName?: string | null;
};

export function StatusBar({
  health,
  overview,
  pollMs,
  rowCount,
  filteredCount,
  displayName,
}: Props) {
  const alive = health?.collector_alive ?? false;
  const feedState = health?.feed_state;
  const collectorLabel =
    feedState === "feed_quiet"
      ? "quiet"
      : feedState === "gap"
        ? "gap"
        : alive
          ? "alive"
          : "down";
  const collectorVariant = collectorLabel === "alive" ? "open" : "warning";
  const session = overview?.session_now ?? null;
  // Prefer API display_name (human) over raw market id fallbacks.
  const fromApi = overview?.display_name ?? health?.display_name;
  const title =
    fromApi ??
    (displayName && displayName !== overview?.market_id && displayName.includes("⇄")
      ? displayName
      : null) ??
    "xStocks · Bybit ⇄ Fluxion";

  // Single horizontal strip: never wrap (age/block ticks used to push a 2nd
  // line and reflow the whole overview). Overflow scrolls on narrow viewports
  // instead of growing height. Local db path is intentionally not shown.
  return (
    <div className="mb-3 overflow-x-auto rounded-md border border-border bg-card">
      <div className="flex h-9 min-w-max items-center gap-x-3 whitespace-nowrap px-3 text-[11px] text-muted-foreground">
        <span className="font-semibold tracking-wide text-foreground">{title}</span>

        <span className="text-border" aria-hidden>
          |
        </span>

        <span className="inline-flex items-center gap-1.5">
          <span
            className={cn(
              "inline-block h-1.5 w-1.5 shrink-0 rounded-full",
              collectorLabel === "alive"
                ? "bg-positive"
                : "bg-warning animate-pulse",
            )}
            aria-hidden
          />
          collector{" "}
          <Badge
            variant={collectorVariant}
            title={health?.recovery_hint ?? undefined}
          >
            {collectorLabel}
          </Badge>
        </span>

        <span className="inline-flex items-center gap-1.5">
          session{" "}
          <Badge variant={session === "open" ? "open" : "closed"}>
            {fmtSession(session)}
          </Badge>
        </span>

        <span>
          data{" "}
          <span className="tabular-nums text-foreground">
            {fmtTsMs(health?.freshest_recv_ts_ms ?? null)}
          </span>
          {/* Fixed-width age slot so "267ms" → "1.2s" does not reflow neighbors. */}
          <span className="ml-1 inline-block w-[5.5rem] tabular-nums">
            {health?.age_ms != null ? `(age ${fmtAgeMs(health.age_ms)})` : ""}
          </span>
        </span>

        <span>
          block{" "}
          <span className="inline-block min-w-[5.5rem] tabular-nums text-foreground">
            {health?.last_block != null ? health.last_block : "—"}
          </span>
        </span>

        <span>
          pairs{" "}
          <span className="tabular-nums text-foreground">
            {filteredCount === rowCount
              ? rowCount
              : `${filteredCount}/${rowCount}`}
          </span>
        </span>

        <span>
          poll{" "}
          <span className="tabular-nums text-foreground">
            {(pollMs / 1000).toFixed(1)}s
          </span>
        </span>

        <span
          title={`Overview Net is at PnL v2 optimal size Q* per row (ADR-0002). Detail EdgeStats remain secondary at fixed M3 ${fmtReferenceSize(overview?.reference_size_usd)}.`}
        >
          net@{" "}
          <span className="tabular-nums text-foreground">Q*</span>
        </span>
      </div>
    </div>
  );
}
