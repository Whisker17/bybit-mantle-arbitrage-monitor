"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import {
  ammQuoteReasonLabel,
  bpsTone,
  cexPremiumBps,
  fmtDirection,
  fmtDirectionTitle,
  fmtNotional,
  fmtPrice,
  fmtSession,
  fmtSignedBps,
  fmtUsd,
  fmtUtcHm,
  fmtVolumeRatio,
  priceTypeBadgeVariant,
  resolveVenues,
  usdTone,
  venueLabel,
  type DirectionVenues,
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

function tvlTitle(row: PairOverviewRow): string {
  if (row.tvl_usd == null) {
    return "Waiting for first TVL sample (balanceOf poll, ~30s cadence)";
  }
  const asOf =
    row.tvl_as_of_ms != null
      ? ` · as of ${fmtUtcHm(row.tvl_as_of_ms)} UTC`
      : "";
  return `Pool TVL ${fmtNotional(row.tvl_usd)}${asOf} — capital size, not depth (PnL v2 buckets)`;
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
  /** Venue ids for group labels + Dir codes (WHI-780). */
  venues?: DirectionVenues | null;
  /** Empty-table message (filter miss vs market accumulating). */
  emptyMessage?: string;
};

/** Column groups for the two-row thead (WHI-780). */
type ColGroup =
  | "identity"
  | "cex"
  | "dex"
  | "edge"
  | "vol_gap"
  | "result"
  | "reference";

type Col = {
  key: SortKey | null;
  label: string;
  group: ColGroup;
  align?: "left" | "right";
  title?: string;
  /** Column is RFQ-only; omitted when hasRfq is false. */
  rfq?: boolean;
  /** Stable id for React keys (labels can repeat across markets). */
  id: string;
};

/**
 * Leaf column order (WHI-780 + WHI-783):
 * CEX Vol + vs Und with CEX L1; DEX Vol with DEX quotes; vs CEX / vs Und
 * explicitly name the basis so DEX vs CEX and DEX vs Und cannot be confused.
 * Underlying group is reference price only (premium lives inside venue groups).
 * To add a group later: append a ColGroup + COLS entries; group row is derived.
 */
const COLS: Col[] = [
  { id: "pair", key: "pair_id", label: "Pair", group: "identity" },
  {
    id: "sess",
    key: null,
    label: "Sess",
    group: "identity",
    title: "NYSE session at last quote",
  },
  { id: "bid", key: null, label: "Bid", group: "cex", align: "right" },
  { id: "ask", key: null, label: "Ask", group: "cex", align: "right" },
  {
    id: "mid",
    key: "bybit_mid",
    label: "Mid",
    group: "cex",
    align: "right",
  },
  {
    id: "cex_vol",
    key: "cex_volume_24h",
    label: "CEX Vol",
    group: "cex",
    align: "right",
    title: "CEX rolling 24h quote volume (exchange REST)",
  },
  {
    id: "cex_vs_und",
    key: "premium_bps",
    label: "vs Und",
    group: "cex",
    align: "right",
    title: "CEX de-multiplied mid vs underlying equity (bps)",
  },
  {
    id: "amm",
    key: null,
    label: "AMM",
    group: "dex",
    align: "right",
    title: "DEX AMM mid",
  },
  {
    id: "rfqb",
    key: null,
    label: "RFQb",
    group: "dex",
    align: "right",
    title: "RFQ buy (taker buys base)",
    rfq: true,
  },
  {
    id: "rfqs",
    key: null,
    label: "RFQs",
    group: "dex",
    align: "right",
    title: "RFQ sell",
    rfq: true,
  },
  {
    id: "amm_bps",
    key: "amm_spread",
    label: "vs CEX",
    group: "dex",
    align: "right",
    title: "AMM mid relative to CEX mid (bps) — core arb spread",
  },
  {
    id: "rfq_bps",
    key: "rfq_spread",
    label: "RFQ vs CEX",
    group: "dex",
    align: "right",
    title: "RFQ mid relative to CEX mid (bps)",
    rfq: true,
  },
  {
    id: "dex_vs_und",
    key: "amm_premium",
    label: "vs Und",
    group: "dex",
    align: "right",
    title:
      "AMM mid vs underlying equity (bps). Hover for RFQ vs underlying when RFQ exists",
  },
  {
    id: "tvl",
    key: "tvl_usd",
    label: "TVL",
    group: "dex",
    align: "right",
    title:
      "DEX pool TVL (token balances × mid). Capital size — not tradeable depth (see PnL v2 buckets). V3 L is not TVL.",
  },
  {
    id: "dex_vol",
    key: "dex_volume_24h",
    label: "DEX Vol",
    group: "dex",
    align: "right",
    title: "DEX AMM swap notional in window (truncated if collector < 24h)",
  },
  {
    id: "net",
    key: "net_edge",
    label: "Net",
    group: "edge",
    align: "right",
    title: "Net edge at ref size (AMM)",
  },
  {
    id: "dir",
    key: null,
    label: "Dir",
    group: "edge",
    title: "Arb direction (market-aware short codes)",
  },
  // Ven (net_edge_venue amm/rfq) dropped from overview — low density; RFQ
  // columns already surface mechanism, and binance-pancake is always amm.
  {
    id: "vol_ratio",
    key: "volume_ratio",
    label: "CEX/DEX",
    group: "vol_gap",
    align: "right",
    title: "CEX ÷ DEX 24h notional ratio (core volume gap metric)",
  },
  {
    id: "bucket_pnl",
    key: null,
    label: "Bucket PnL",
    group: "result",
    align: "right",
    title: "Optimal size net PnL (PnL v2) — hover for direction & notional",
  },
  {
    id: "mm",
    key: null,
    label: "MM",
    group: "result",
    title:
      "Market-maker trade activity in lookback window: active / inactive / unknown",
  },
  {
    id: "underlying",
    key: "underlying_price",
    label: "Price",
    group: "reference",
    align: "right",
    title: "Underlying equity reference (Pyth/Yahoo) + price_type badge",
  },
];

type GroupStrip = {
  group: ColGroup;
  label: string;
  colSpan: number;
};

function groupDisplayLabel(
  group: ColGroup,
  venues: DirectionVenues,
): string {
  switch (group) {
    case "identity":
      return "";
    case "cex":
      return venueLabel(venues.cex);
    case "dex":
      return venueLabel(venues.dex);
    case "edge":
      return "Edge";
    case "vol_gap":
      return "CEX÷DEX";
    case "result":
      // Spec group name is Result (Bucket PnL + MM); "PnL" alone mislabels MM.
      return "Result";
    case "reference":
      return "Underlying";
  }
}

function buildGroupStrip(cols: Col[], venues: DirectionVenues): GroupStrip[] {
  const out: GroupStrip[] = [];
  for (const col of cols) {
    const last = out[out.length - 1];
    if (last && last.group === col.group) {
      last.colSpan += 1;
      continue;
    }
    out.push({
      group: col.group,
      label: groupDisplayLabel(col.group, venues),
      colSpan: 1,
    });
  }
  return out;
}

function BpsCell({
  value,
  emptyLabel,
  emptyTitle,
}: {
  value: string | null;
  /** When value is null, show this instead of em-dash (e.g. empty pool). */
  emptyLabel?: string | null;
  emptyTitle?: string | null;
}) {
  if (value == null && emptyLabel) {
    return (
      <span
        className="text-muted-foreground"
        title={emptyTitle ?? emptyLabel}
      >
        {emptyLabel}
      </span>
    );
  }
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

/** AMM mid / price cell with empty-pool reason (WHI-795). */
function AmmMidCell({ row }: { row: PairOverviewRow }) {
  const reason = ammQuoteReasonLabel(row.amm_quote_reason);
  if (row.amm_mid == null && reason) {
    return (
      <span
        className="text-muted-foreground"
        title="Pool has zero in-range liquidity — residual slot0 mid suppressed"
      >
        {reason}
      </span>
    );
  }
  return (
    <span className="tabular-nums">{fmtPrice(row.amm_mid)}</span>
  );
}

function BucketPnlCell({
  row,
  venues,
  marketId,
}: {
  row: PairOverviewRow;
  venues: DirectionVenues;
  marketId: string;
}) {
  const cell = overviewPnlCell(row.pnl_v2, venues, marketId);
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

/**
 * Venue vs-underlying bps cell (WHI-783). Shared private/empty handling;
 * titleParts name who-vs-whom (kept on hover even when the primary value
 * is missing so RFQ-only hover lines still surface).
 */
function VsUndCell({
  row,
  value,
  emptyTitle,
  titleParts,
}: {
  row: PairOverviewRow;
  value: string | null;
  emptyTitle: string;
  titleParts: Array<string | null | false | undefined>;
}) {
  if (row.underlying_empty === "private") {
    return (
      <span
        className="text-muted-foreground"
        title="Private underlying — no premium"
      >
        n/a
      </span>
    );
  }
  const parts = titleParts.filter(Boolean) as string[];
  // When the primary bps is missing, always keep the empty-state explanation
  // (do not replace it with a lone "Underlying: vs close" label).
  const title =
    value == null
      ? [...parts, emptyTitle].filter(Boolean).join(" · ")
      : parts.join(" · ");
  if (value == null) {
    return (
      <span className="text-muted-foreground" title={title || emptyTitle}>
        —
      </span>
    );
  }
  return (
    <span title={title || undefined}>
      <BpsCell value={value} />
    </span>
  );
}

/** CEX group "vs Und": CEX equity-eq mid vs underlying. */
function CexVsUndCell({ row }: { row: PairOverviewRow }) {
  const value = cexPremiumBps(row);
  return (
    <VsUndCell
      row={row}
      value={value}
      emptyTitle="Needs de-multiplied CEX mid + underlying print"
      titleParts={[
        value != null
          ? `CEX mid vs underlying: ${fmtSignedBps(value)} bps`
          : null,
        row.premium_type_label ? `Underlying: ${row.premium_type_label}` : null,
      ]}
    />
  );
}

/**
 * DEX group "vs Und": AMM mid vs underlying; RFQ vs Und in hover when present
 * (no empty RFQ column — WHI-783).
 */
function DexVsUndCell({
  row,
  hasRfq,
}: {
  row: PairOverviewRow;
  hasRfq: boolean;
}) {
  const amm = row.amm_premium_bps ?? null;
  const emptyPool = ammQuoteReasonLabel(row.amm_quote_reason);
  if (amm == null && emptyPool) {
    return (
      <span
        className="text-muted-foreground"
        title="Pool has zero in-range liquidity — residual mid suppressed"
      >
        {emptyPool}
      </span>
    );
  }
  return (
    <VsUndCell
      row={row}
      value={amm}
      emptyTitle="Needs AMM mid + underlying print"
      titleParts={[
        amm != null
          ? `AMM mid vs underlying: ${fmtSignedBps(amm)} bps`
          : null,
        hasRfq && row.rfq_premium_bps != null
          ? `RFQ mid vs underlying: ${fmtSignedBps(row.rfq_premium_bps)} bps`
          : null,
        row.premium_type_label ? `Underlying: ${row.premium_type_label}` : null,
      ]}
    />
  );
}

/** Light left rule between groups (first leaf of each non-identity group). */
function groupSep(group: ColGroup): string {
  return group !== "identity" ? "border-l border-border/70" : "";
}

export function PairsTable({
  rows,
  sortKey,
  sortDesc,
  onSort,
  marketId,
  hasRfq = true,
  venues: venuesProp,
  emptyMessage = "No pairs match the current filter.",
}: Props) {
  const router = useRouter();
  const venues = useMemo(
    () => resolveVenues(venuesProp, marketId),
    [venuesProp, marketId],
  );
  const cols = useMemo(
    () => COLS.filter((c) => hasRfq || !c.rfq),
    [hasRfq],
  );
  const groups = useMemo(() => buildGroupStrip(cols, venues), [cols, venues]);
  // First leaf id per group — for separator styling on body cells.
  const groupFirstId = useMemo(() => {
    const map = new Map<ColGroup, string>();
    for (const c of cols) {
      if (!map.has(c.group)) map.set(c.group, c.id);
    }
    return map;
  }, [cols]);

  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table
        className={cn(
          "w-full border-collapse text-xs",
          // WHI-783: +1 vs Und in CEX, +1 vs Und in DEX; Premium column removed.
          hasRfq ? "min-w-[1400px]" : "min-w-[1220px]",
        )}
      >
        <thead>
          {/* Group strip — venue-named CEX/DEX bars (WHI-780). */}
          <tr className="border-b border-border/80 bg-muted/50 text-[10px] uppercase tracking-wide text-muted-foreground">
            {groups.map((g) => (
              <th
                key={g.group}
                colSpan={g.colSpan}
                className={cn(
                  "whitespace-nowrap px-2 py-1 font-semibold text-center",
                  groupSep(g.group),
                  g.label ? "text-foreground/80" : "text-transparent",
                )}
              >
                {g.label || "\u00a0"}
              </th>
            ))}
          </tr>
          {/* Leaf labels. */}
          <tr className="border-b border-border bg-muted/40 text-muted-foreground">
            {cols.map((col) => {
              const sortable = col.key != null;
              const active = col.key === sortKey;
              const arrow = active ? (sortDesc ? " ↓" : " ↑") : "";
              const isGroupStart = groupFirstId.get(col.group) === col.id;
              return (
                <th
                  key={col.id}
                  title={col.title}
                  className={cn(
                    "whitespace-nowrap px-2 py-2 font-medium",
                    col.align === "right" ? "text-right" : "text-left",
                    sortable &&
                      "cursor-pointer select-none hover:text-foreground",
                    active && "text-foreground",
                    isGroupStart && groupSep(col.group),
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
              const dirTitle = fmtDirectionTitle(
                row.net_edge_direction,
                venues,
                marketId,
              );
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
                  {/* Identity */}
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
                  {/* CEX: Bid Ask Mid CEX Vol */}
                  <td
                    className={cn(
                      "px-2 py-1.5 text-right tabular-nums",
                      groupSep("cex"),
                    )}
                  >
                    {fmtPrice(row.bybit_bid)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {fmtPrice(row.bybit_ask)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {fmtPrice(row.bybit_mid)}
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
                  <td className="px-2 py-1.5 text-right">
                    <CexVsUndCell row={row} />
                  </td>
                  {/* DEX: AMM RFQ* vs CEX / vs Und / DEX Vol */}
                  <td
                    className={cn(
                      "px-2 py-1.5 text-right tabular-nums",
                      groupSep("dex"),
                    )}
                  >
                    <AmmMidCell row={row} />
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
                    <BpsCell
                      value={row.amm_spread_bps}
                      emptyLabel={ammQuoteReasonLabel(row.amm_quote_reason)}
                      emptyTitle="Pool has zero in-range liquidity — residual mid suppressed"
                    />
                  </td>
                  {hasRfq && (
                    <td className="px-2 py-1.5 text-right">
                      <BpsCell value={row.rfq_spread_bps} />
                    </td>
                  )}
                  <td className="px-2 py-1.5 text-right">
                    <DexVsUndCell row={row} hasRfq={hasRfq} />
                  </td>
                  <td
                    className="px-2 py-1.5 text-right tabular-nums"
                    title={tvlTitle(row)}
                  >
                    {fmtNotional(row.tvl_usd)}
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
                  {/* Edge */}
                  <td
                    className={cn(
                      "px-2 py-1.5 text-right font-medium",
                      groupSep("edge"),
                    )}
                  >
                    <BpsCell
                      value={row.net_edge_bps}
                      emptyLabel={ammQuoteReasonLabel(row.amm_quote_reason)}
                      emptyTitle="Pool has zero in-range liquidity — no net edge"
                    />
                  </td>
                  <td
                    className="px-2 py-1.5 text-muted-foreground"
                    title={dirTitle}
                  >
                    {fmtDirection(row.net_edge_direction, venues, marketId)}
                  </td>
                  {/* Vol gap */}
                  <td
                    className={cn(
                      "px-2 py-1.5 text-right tabular-nums text-muted-foreground",
                      groupSep("vol_gap"),
                    )}
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
                  {/* Result: Bucket PnL, MM */}
                  <td
                    className={cn("px-2 py-1.5 text-right", groupSep("result"))}
                  >
                    <BucketPnlCell
                      row={row}
                      venues={venues}
                      marketId={marketId}
                    />
                  </td>
                  <td className="px-2 py-1.5">
                    <MmActiveCell status={row.mm_active} />
                  </td>
                  {/* Underlying group: reference price only (WHI-783) */}
                  <td
                    className={cn(
                      "px-2 py-1.5 text-right",
                      groupSep("reference"),
                    )}
                  >
                    <UnderlyingCell row={row} />
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
