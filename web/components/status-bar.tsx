import { Badge } from "@/components/ui/badge";
import { fmtAgeMs, fmtSession, fmtTsMs } from "@/lib/format";
import type { HealthResponse, OverviewResponse } from "@/lib/types";
import { cn } from "@/lib/cn";

type Props = {
  health: HealthResponse | null;
  overview: OverviewResponse | null;
  pollMs: number;
  rowCount: number;
  filteredCount: number;
};

export function StatusBar({
  health,
  overview,
  pollMs,
  rowCount,
  filteredCount,
}: Props) {
  const alive = health?.collector_alive ?? false;
  const session = overview?.session_now ?? null;

  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md border border-border bg-card px-3 py-2 text-[11px] text-muted-foreground">
      <span className="font-semibold tracking-wide text-foreground">
        xStocks · Bybit ⇄ Fluxion
      </span>

      <span className="text-border">|</span>

      <span className="inline-flex items-center gap-1.5">
        <span
          className={cn(
            "inline-block h-1.5 w-1.5 rounded-full",
            alive ? "bg-positive" : "bg-warning animate-pulse",
          )}
          aria-hidden
        />
        collector{" "}
        <Badge variant={alive ? "ok" : "warning"}>
          {alive ? "alive" : "down"}
        </Badge>
      </span>

      <span>
        session{" "}
        <Badge variant={session === "open" ? "open" : "closed"}>
          {fmtSession(session)}
        </Badge>
      </span>

      <span>
        data{" "}
        <span className="text-foreground">
          {fmtTsMs(health?.freshest_recv_ts_ms ?? null)}
        </span>
        {health?.age_ms != null && (
          <span className="ml-1">(age {fmtAgeMs(health.age_ms)})</span>
        )}
      </span>

      <span>
        block{" "}
        <span className="text-foreground">
          {health?.last_block != null ? health.last_block : "—"}
        </span>
      </span>

      <span>
        pairs{" "}
        <span className="text-foreground">
          {filteredCount === rowCount
            ? rowCount
            : `${filteredCount}/${rowCount}`}
        </span>
      </span>

      <span>
        poll{" "}
        <span className="text-foreground">{(pollMs / 1000).toFixed(1)}s</span>
      </span>

      {overview?.reference_size_usd != null && (
        <span>
          net@{" "}
          <span className="text-foreground">
            ${Number(overview.reference_size_usd).toLocaleString()}
          </span>
        </span>
      )}

      {overview?.db_path && (
        <span className="ml-auto max-w-[40%] truncate" title={overview.db_path}>
          db {overview.db_path}
        </span>
      )}
    </div>
  );
}
