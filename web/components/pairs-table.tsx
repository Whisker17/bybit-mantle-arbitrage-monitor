"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import {
  bpsTone,
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
import {
  BADGE_LABEL_TVL,
  BADGE_LABEL_VOL,
  badgePolicyForMarket,
  pairBadgesForRows,
} from "@/lib/pair-badges";
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
  /** Venue ids for group labels + Dir codes (WHI-780). */
  venues?: DirectionVenues | null;
  /** Empty-table message (filter miss vs market accumulating). */
  emptyMessage?: string;
  /**
   * Full market overview rows used for TVL/Vol badge ranking (WHI-781).
   * Must be the unfiltered set so hide-low-liq / search does not change ranks.
   * Defaults to `rows` only when the parent already passes the full set.
   */
  badgeSourceRows?: PairOverviewRow[];
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
 * Leaf column order (WHI-780): CEX Vol sits with CEX L1; DEX Vol with DEX quotes.
 * WHI-779 Underlying/Premium live in the Reference group after Result.
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
    label: "AMM bps",
    group: "dex",
    align: "right",
  },
  {
    id: "rfq_bps",
    key: "rfq_spread",
    label: "RFQ bps",
    group: "dex",
    align: "right",
    rfq: true,
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
  { id: "ven", key: null, label: "Ven", group: "edge" },
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
    label: "Underlying",
    group: "reference",
    align: "right",
    title: "Underlying equity reference (Pyth/Yahoo) + price_type badge",
  },
  {
    id: "premium",
    key: "premium_bps",
    label: "Premium",
    group: "reference",
    align: "right",
    title:
      "CEX de-multiplied mid vs underlying (bps). Hover for AMM/RFQ premiums",
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
  badgeSourceRows,
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

  // WHI-781: rank on full market set; tooltips from market policy.
  const badgePolicy = useMemo(
    () => badgePolicyForMarket(marketId),
    [marketId],
  );
  const badgesByPair = useMemo(
    () => pairBadgesForRows(badgeSourceRows ?? rows, marketId, badgePolicy),
    [badgeSourceRows, rows, marketId, badgePolicy],
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
              const badges = badgesByPair.get(row.pair_id);
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
                      {badges?.hiTvl && (
                        <Badge
                          variant="positive"
                          className="normal-case"
                          title={badgePolicy.tvlTitle}
                        >
                          {BADGE_LABEL_TVL}
                        </Badge>
                      )}
                      {badges?.hiVol && (
                        <Badge
                          variant="default"
                          className="normal-case"
                          title={badgePolicy.volTitle}
                        >
                          {BADGE_LABEL_VOL}
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
                  {/* DEX: AMM RFQ* bps DEX Vol */}
                  <td
                    className={cn(
                      "px-2 py-1.5 text-right tabular-nums",
                      groupSep("dex"),
                    )}
                  >
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
                    <BpsCell value={row.net_edge_bps} />
                  </td>
                  <td
                    className="px-2 py-1.5 text-muted-foreground"
                    title={dirTitle}
                  >
                    {fmtDirection(row.net_edge_direction, venues, marketId)}
                  </td>
                  <td className="px-2 py-1.5 uppercase text-muted-foreground">
                    {row.net_edge_venue ?? "—"}
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
                  {/* Reference: Underlying, Premium (WHI-779) */}
                  <td
                    className={cn(
                      "px-2 py-1.5 text-right",
                      groupSep("reference"),
                    )}
                  >
                    <UnderlyingCell row={row} />
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <PremiumCell row={row} />
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
