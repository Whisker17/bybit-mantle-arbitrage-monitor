"use client";

/**
 * CEX vs DEX 24h volume compare mini-panel (WHI-777).
 */

import {
  fmtNotional,
  fmtUtcHm,
  fmtVolumeRatio,
} from "@/lib/format";
import type { VolumeCompare } from "@/lib/types";
import { cn } from "@/lib/cn";

type Props = {
  volume: VolumeCompare | null | undefined;
  className?: string;
};

function SliceRow({
  label,
  volume,
  count,
}: {
  label: string;
  volume: string;
  count: number;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular-nums">
        {fmtNotional(volume)}
        <span className="ml-1 text-muted-foreground">· {count} tx</span>
      </span>
    </div>
  );
}

export function VolumePanel({ volume, className }: Props) {
  if (volume == null) {
    return (
      <div
        className={cn(
          "rounded-md border border-border/60 bg-card/40 px-3 py-3 text-xs text-muted-foreground",
          className,
        )}
      >
        Volume compare not available yet (waiting for journal / CEX poll).
      </div>
    );
  }

  const dex = volume.dex;
  const truncatedNote =
    dex.truncated && dex.window_start_ms != null
      ? `DEX window truncated · since ${fmtUtcHm(dex.window_start_ms)} UTC`
      : "DEX window: rolling 24h of collected swaps";

  return (
    <div
      className={cn(
        "grid gap-3 rounded-md border border-border/60 bg-card/40 px-3 py-3 sm:grid-cols-2",
        className,
      )}
    >
      <div className="space-y-1.5">
        <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          CEX 24h (REST)
        </div>
        <div className="text-sm font-medium tabular-nums">
          {fmtNotional(volume.cex_volume_24h)}
          {volume.cex_trade_count_24h != null ? (
            <span className="ml-1 text-xs font-normal text-muted-foreground">
              · {volume.cex_trade_count_24h} prints
            </span>
          ) : null}
        </div>
        <div className="text-[11px] text-muted-foreground">
          {volume.cex_source
            ? `Source: ${volume.cex_source}`
            : "No poll yet"}
        </div>
        {volume.cex_journal != null ? (
          <div className="mt-2 space-y-1 border-t border-border/40 pt-2">
            <div className="text-[11px] text-muted-foreground">
              Journal session split (partial if collector &lt; 24h)
              {volume.cex_journal.truncated ? " · truncated" : ""}
            </div>
            <SliceRow
              label="Open"
              volume={volume.cex_journal.open.volume_usd}
              count={volume.cex_journal.open.trade_count}
            />
            <SliceRow
              label="Closed"
              volume={volume.cex_journal.closed.volume_usd}
              count={volume.cex_journal.closed.trade_count}
            />
          </div>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          DEX 24h (swaps)
        </div>
        <div className="text-sm font-medium tabular-nums">
          {fmtNotional(dex.volume_usd)}
          <span className="ml-1 text-xs font-normal text-muted-foreground">
            · {dex.trade_count} swaps
          </span>
          {dex.truncated ? (
            <span className="ml-1 text-[10px] text-amber-400/90">truncated</span>
          ) : null}
        </div>
        <div className="text-[11px] text-muted-foreground">{truncatedNote}</div>
        <div className="mt-2 space-y-1 border-t border-border/40 pt-2">
          <SliceRow
            label="Open"
            volume={dex.open.volume_usd}
            count={dex.open.trade_count}
          />
          <SliceRow
            label="Closed"
            volume={dex.closed.volume_usd}
            count={dex.closed.trade_count}
          />
        </div>
      </div>

      <div className="sm:col-span-2 flex flex-wrap items-baseline justify-between gap-2 border-t border-border/40 pt-2">
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          CEX / DEX ratio
        </span>
        <span
          className="text-base font-semibold tabular-nums"
          title={
            volume.volume_ratio != null
              ? String(volume.volume_ratio)
              : "Needs CEX poll and positive DEX volume"
          }
        >
          {fmtVolumeRatio(volume.volume_ratio)}
        </span>
      </div>
    </div>
  );
}
