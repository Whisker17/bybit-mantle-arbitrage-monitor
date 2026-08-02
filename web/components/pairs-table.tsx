"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import {
  bpsTone,
  fmtDirection,
  fmtNotional,
  fmtPrice,
  fmtSession,
  fmtSignedBps,
  fmtUsd,
  fmtUtcHm,
  fmtVolumeRatio,
  priceTypeBadgeVariant,
  usdTone,
} from "@/lib/format";
import { marketPairPath } from "@/lib/markets";
import { mmActiveLabel, mmActiveTitle } from "@/lib/mm";
import { overviewPnlCell } from "@/lib/pnl";
import type { MmActiveStatus, PairOverviewRow, SortKey } from "@/lib/types";

function dexVolumeTitle(row: PairOverviewRow): string {
  const base =
    row.dex_volume_24h != null
      ? `DEX window notional ${row.dex_volume_24h}`
      : "No DEX swaps in window";
  if (row.dex_volume_truncated && row.dex_volume_window_start_ms != null) {
    return `${base} · truncated since ${fmtUtcHm(row.dex_volume_window_start_ms)} UTC (collector < 24h)`;
  }
  return base;
}

type Props = {
  rows: PairOverviewRow[];
  sortKey: SortKey;
  sortDesc: boolean;
  onSort: (key: SortKey) => void;
  /** Market id for row → detail links (WHI-774). */
  marketId: string;
  /** Hide RFQ columns entirely when the market has no RFQ (binance-pancake). */
  hasRfq?: boolean;
  /** Empty-table message (filter miss vs market accumulating). */
  emptyMessage?: string;
};

type Col = {
  key: SortKey | null;
  label: string;
  align?: "left" | "right";
  title?: string;
  /** Column is RFQ-only; omitted when hasRfq is false. */
  rfq?: boolean;
};

const COLS: Col[] = [
  { key: "pair_id", label: "Pair" },
  { key: null, label: "Sess", title: "NYSE session at last quote" },
  { key: null, label: "Bid", align: "right" },
  { key: null, label: "Ask", align: "right" },
  { key: "bybit_mid", label: "Mid", align: "right" },
  { key: null, label: "AMM", align: "right", title: "Fluxion AMM mid" },
  {
    key: null,
    label: "RFQb",
    align: "right",
    title: "RFQ buy (taker buys base)",
    rfq: true,
  },
  { key: null, label: "RFQs", align: "right", title: "RFQ sell", rfq: true },
  { key: "amm_spread", label: "AMM bps", align: "right" },
  {
    key: "rfq_spread",
    label: "RFQ bps",
    align: "right",
    rfq: true,
  },
  {
    key: "net_edge",
    label: "Net",
    align: "right",
    title: "Net edge at ref size (AMM)",
  },
  { key: null, label: "Dir", title: "Arb direction" },
  { key: null, label: "Ven" },
  {
    key: "cex_volume_24h",
    label: "CEX Vol",
    align: "right",
    title: "CEX rolling 24h quote volume (exchange REST)",
  },
  {
    key: "dex_volume_24h",
    label: "DEX Vol",
    align: "right",
    title: "DEX AMM swap notional in window (truncated if collector < 24h)",
  },
  {
    key: "volume_ratio",
    label: "CEX/DEX",
    align: "right",
    title: "CEX ÷ DEX 24h notional ratio (core volume gap metric)",
  },
  {
    key: "underlying_price",
    label: "Underlying",
    align: "right",
    title: "Underlying equity reference (Pyth/Yahoo) + price_type badge",
  },
  {
    key: "premium_bps",
    label: "Premium",
    align: "right",
    title:
      "CEX de-multiplied mid vs underlying (bps). Hover for AMM/RFQ premiums",
  },
  {
    key: null,
    label: "Bucket PnL",
    align: "right",
    title: "Optimal size net PnL (PnL v2) — hover for direction & notional",
  },
  {
    key: null,
    label: "MM",
    title:
      "Market-maker trade activity in lookback window: active / inactive / unknown",
  },
];

function BpsCell({ value }: { value: string | null }) {
  const tone = bpsTone(value);
  return (
    <span
      className={cn(
        "tabular-nums",
        tone === "pos" && "text-positive",
        tone === "neg" && "text-negative",
        tone === "empty" && "text-muted-foreground",
      )}
    >
      {fmtSignedBps(value)}
    </span>
  );
}

function BucketPnlCell({ row }: { row: PairOverviewRow }) {
  const cell = overviewPnlCell(row.pnl_v2);
  if (cell.kind === "ok") {
    const tone = usdTone(cell.pnlUsd);
    return (
      <span
        className={cn(
          "tabular-nums font-medium",
          tone === "pos" && "text-positive",
          tone === "neg" && "text-negative",
          tone === "empty" && "text-muted-foreground",
        )}
        title={cell.title}
      >
        {fmtUsd(cell.pnlUsd)}
      </span>
    );
  }
  return (
    <span className="text-muted-foreground" title={cell.title}>
      {cell.label}
    </span>
  );
}

function MmActiveCell({ status }: { status: MmActiveStatus | null | undefined }) {
  const s = status ?? "unknown";
  if (s === "active") {
    return (
      <Badge variant="mm" className="normal-case" title={mmActiveTitle(s)}>
        {mmActiveLabel(s)}
      </Badge>
    );
  }
  return (
    <span className="text-muted-foreground" title={mmActiveTitle(s)}>
      {mmActiveLabel(s)}
    </span>
  );
}

function UnderlyingCell({ row }: { row: PairOverviewRow }) {
  if (row.underlying_empty === "private") {
    return (
      <span
        className="text-muted-foreground"
        title={`${row.underlying_ticker ?? "underlying"} is private — no public equity feed`}
      >
        n/a private
      </span>
    );
  }
  if (row.underlying_price == null) {
    return (
      <span
        className="text-muted-foreground"
        title={
          row.underlying_ticker
            ? `Waiting for underlying print (${row.underlying_ticker})`
            : "No underlying ticker"
        }
      >
        —
      </span>
    );
  }
  const badge = row.underlying_price_type ?? row.premium_type_label;
  return (
    <span
      className="inline-flex items-center justify-end gap-1"
      title={
        row.underlying_as_of_ms != null
          ? `${row.underlying_ticker} ${row.underlying_source ?? ""} as_of ${new Date(row.underlying_as_of_ms).toISOString()}`
          : (row.underlying_ticker ?? undefined)
      }
    >
      <span className="tabular-nums">{fmtPrice(row.underlying_price)}</span>
      {badge && (
        <Badge
          variant={priceTypeBadgeVariant(row.underlying_price_type)}
          className="normal-case"
        >
          {row.underlying_price_type ?? badge}
        </Badge>
      )}
    </span>
  );
}

function PremiumCell({ row }: { row: PairOverviewRow }) {
  if (row.underlying_empty === "private") {
    return (
      <span className="text-muted-foreground" title="Private underlying — no premium">
        n/a
      </span>
    );
  }
  if (row.premium_bps == null) {
    return (
      <span
        className="text-muted-foreground"
        title="Needs de-multiplied CEX mid + underlying print"
      >
        —
      </span>
    );
  }
  const title = [
    `CEX vs underlying: ${fmtSignedBps(row.premium_bps)} bps`,
    row.amm_premium_bps != null
      ? `AMM vs underlying: ${fmtSignedBps(row.amm_premium_bps)} bps`
      : null,
    row.rfq_premium_bps != null
      ? `RFQ vs underlying: ${fmtSignedBps(row.rfq_premium_bps)} bps`
      : null,
    row.premium_type_label ? `Underlying: ${row.premium_type_label}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <span className="inline-flex items-center justify-end gap-1" title={title}>
      <BpsCell value={row.premium_bps} />
      {row.premium_type_label &&
        row.underlying_price_type &&
        row.underlying_price_type !== "live" && (
          <Badge
            variant={priceTypeBadgeVariant(row.underlying_price_type)}
            className="normal-case"
          >
            {row.premium_type_label}
          </Badge>
        )}
    </span>
  );
}

export function PairsTable({
  rows,
  sortKey,
  sortDesc,
  onSort,
  marketId,
  hasRfq = true,
  emptyMessage = "No pairs match the current filter.",
}: Props) {
  const router = useRouter();
  const cols = useMemo(
    () => COLS.filter((c) => hasRfq || !c.rfq),
    [hasRfq],
  );

  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table
        className={cn(
          "w-full border-collapse text-xs",
          hasRfq ? "min-w-[1320px]" : "min-w-[1140px]",
        )}
      >
        <thead>
          <tr className="border-b border-border bg-muted/40 text-muted-foreground">
            {cols.map((col) => {
              const sortable = col.key != null;
              const active = col.key === sortKey;
              const arrow = active ? (sortDesc ? " ↓" : " ↑") : "";
              return (
                <th
                  key={col.label}
                  title={col.title}
                  className={cn(
                    "whitespace-nowrap px-2 py-2 font-medium",
                    col.align === "right" ? "text-right" : "text-left",
                    sortable &&
                      "cursor-pointer select-none hover:text-foreground",
                    active && "text-foreground",
                  )}
                  onClick={() => {
                    if (col.key) onSort(col.key);
                  }}
                  aria-sort={
                    active ? (sortDesc ? "descending" : "ascending") : undefined
                  }
                >
                  {col.label}
                  {arrow}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={cols.length}
                className="px-3 py-8 text-center text-muted-foreground"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            rows.map((row) => {
              const dim = row.low_liquidity || row.stale;
              const href = marketPairPath(marketId, row.pair_id);
              return (
                <tr
                  key={row.pair_id}
                  className={cn(
                    "border-b border-border/60 transition-colors hover:bg-muted/50 cursor-pointer",
                    dim && "opacity-50",
                  )}
                  onClick={() => {
                    // Don't navigate when the operator is drag-selecting a price to copy.
                    const sel = window.getSelection();
                    if (sel && !sel.isCollapsed && sel.toString().length > 0) {
                      return;
                    }
                    router.push(href);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      router.push(href);
                    }
                  }}
                  tabIndex={0}
                >
                  <td className="px-2 py-1.5 font-medium text-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      <Link
                        href={href}
                        className="hover:underline underline-offset-2 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded-sm"
                        onClick={(e) => {
                          // Let the row handler navigate; avoid double push.
                          e.stopPropagation();
                        }}
                      >
                        {row.pair_id}
                      </Link>
                      {row.low_liquidity && (
                        <Badge variant="muted" className="normal-case">
                          low-liq
                        </Badge>
                      )}
                      {row.stale && (
                        <Badge variant="warning" className="normal-case">
                          stale
                        </Badge>
                      )}
                    </span>
                  </td>
                  <td className="px-2 py-1.5">
                    <Badge
                      variant={
                        row.session === "open"
                          ? "open"
                          : row.session === "closed"
                            ? "closed"
                            : "muted"
                      }
                    >
                      {fmtSession(row.session)}
                    </Badge>
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {fmtPrice(row.bybit_bid)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {fmtPrice(row.bybit_ask)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {fmtPrice(row.bybit_mid)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {fmtPrice(row.amm_mid)}
                  </td>
                  {hasRfq && (
                    <>
                      <td className="px-2 py-1.5 text-right tabular-nums">
                        {fmtPrice(row.rfq_buy)}
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums">
                        {fmtPrice(row.rfq_sell)}
                      </td>
                    </>
                  )}
                  <td className="px-2 py-1.5 text-right">
                    <BpsCell value={row.amm_spread_bps} />
                  </td>
                  {hasRfq && (
                    <td className="px-2 py-1.5 text-right">
                      <BpsCell value={row.rfq_spread_bps} />
                    </td>
                  )}
                  <td className="px-2 py-1.5 text-right font-medium">
                    <BpsCell value={row.net_edge_bps} />
                  </td>
                  <td className="px-2 py-1.5 text-muted-foreground">
                    {fmtDirection(row.net_edge_direction)}
                  </td>
                  <td className="px-2 py-1.5 uppercase text-muted-foreground">
                    {row.net_edge_venue ?? "—"}
                  </td>
                  <td
                    className="px-2 py-1.5 text-right tabular-nums"
                    title={
                      row.cex_volume_24h != null
                        ? `CEX 24h ${row.cex_volume_24h}`
                        : "Waiting for CEX volume poll"
                    }
                  >
                    {fmtNotional(row.cex_volume_24h)}
                  </td>
                  <td
                    className="px-2 py-1.5 text-right tabular-nums"
                    title={dexVolumeTitle(row)}
                  >
                    {fmtNotional(row.dex_volume_24h)}
                    {row.dex_volume_truncated ? (
                      <span className="ml-0.5 text-[10px] text-amber-400/90">
                        *
                      </span>
                    ) : null}
                  </td>
                  <td
                    className="px-2 py-1.5 text-right tabular-nums text-muted-foreground"
                    title={
                      row.volume_ratio != null
                        ? row.dex_volume_truncated
                          ? `CEX/DEX = ${row.volume_ratio} (DEX window truncated — ratio mixes full CEX 24h vs partial DEX)`
                          : `CEX/DEX = ${row.volume_ratio}`
                        : "Ratio needs positive DEX volume and a CEX poll"
                    }
                  >
                    {fmtVolumeRatio(row.volume_ratio)}
                    {row.dex_volume_truncated && row.volume_ratio != null ? (
                      <span className="ml-0.5 text-[10px] text-amber-400/90">
                        *
                      </span>
                    ) : null}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <UnderlyingCell row={row} />
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <PremiumCell row={row} />
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <BucketPnlCell row={row} />
                  </td>
                  <td className="px-2 py-1.5">
                    <MmActiveCell status={row.mm_active} />
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
