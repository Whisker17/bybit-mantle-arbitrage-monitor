import { cexPremiumBps, parseNum } from "./format";
import type {
  DexNonTradeableReason,
  PairOverviewRow,
  SortKey,
} from "./types";

/**
 * Sortable optimal PnL when status is ok (WHI-824).
 * Prefer flat row fields; fall back to nested pnl_v2 for older payloads.
 * Non-ok statuses (no_depth / no_pool / empty_pool / …) → null (nulls last).
 */
function pnlSortValue(
  row: PairOverviewRow,
  which: "usd" | "bps",
): number | null {
  const flat =
    which === "usd" ? row.pnl_optimal_net_usd : row.pnl_optimal_net_bps;
  if (flat !== undefined && flat !== null) {
    return parseNum(flat);
  }
  const pnl = row.pnl_v2;
  if (pnl == null || pnl.status !== "ok") return null;
  return parseNum(
    which === "usd" ? pnl.optimal_net_pnl_usd : pnl.optimal_net_pnl_bps,
  );
}

function rawValue(
  row: PairOverviewRow,
  key: SortKey,
): number | string | null {
  switch (key) {
    case "pair_id":
      return row.pair_id;
    case "net_edge":
      return parseNum(row.net_edge_bps);
    case "amm_spread":
      return parseNum(row.amm_spread_bps);
    case "rfq_spread":
      return parseNum(row.rfq_spread_bps);
    case "bybit_mid":
      return parseNum(row.bybit_mid);
    case "volume_24h":
      return parseNum(row.volume_24h);
    case "trades_24h":
      return row.trades_24h;
    case "cex_volume_24h":
      return parseNum(row.cex_volume_24h ?? null);
    case "dex_volume_24h":
      return parseNum(row.dex_volume_24h ?? null);
    case "volume_ratio":
      return parseNum(row.volume_ratio ?? null);
    case "premium_bps":
      // Sort key id kept as premium_bps (API-compat); value is CEX vs Und.
      return parseNum(cexPremiumBps(row));
    case "amm_premium":
      return parseNum(row.amm_premium_bps ?? null);
    case "underlying_price":
      return parseNum(row.underlying_price ?? null);
    case "tvl_usd":
      return parseNum(row.tvl_usd ?? null);
    case "pnl_optimal_usd":
      return pnlSortValue(row, "usd");
    case "pnl_optimal_bps":
      return pnlSortValue(row, "bps");
  }
}

/**
 * Stable sort with null / missing values last regardless of direction
 * (mirrors monitor.tui.format.sort_rows).
 */
export function sortRows(
  rows: PairOverviewRow[],
  key: SortKey,
  desc: boolean,
): PairOverviewRow[] {
  const present = rows.filter((r) => hasSortValue(r, key));
  const missing = rows.filter((r) => !hasSortValue(r, key));

  const sorted = [...present].sort((a, b) => {
    const va = rawValue(a, key);
    const vb = rawValue(b, key);
    if (va === null || vb === null) return 0;
    let cmp = 0;
    if (typeof va === "string" && typeof vb === "string") {
      cmp = va.localeCompare(vb);
    } else {
      cmp = Number(va) - Number(vb);
    }
    return desc ? -cmp : cmp;
  });

  return sorted.concat(missing);
}

export function filterRows(
  rows: PairOverviewRow[],
  opts: {
    query: string;
    hideLowLiquidity: boolean;
    hideStale: boolean;
  },
): PairOverviewRow[] {
  const q = opts.query.trim().toLowerCase();
  return rows.filter((r) => {
    if (opts.hideLowLiquidity && r.low_liquidity) return false;
    if (opts.hideStale && r.stale) return false;
    if (!q) return true;
    return (
      r.pair_id.toLowerCase().includes(q) || r.name.toLowerCase().includes(q)
    );
  });
}

export const SORT_KEYS: { key: SortKey; label: string }[] = [
  { key: "net_edge", label: "Net edge" },
  { key: "amm_spread", label: "vs CEX" },
  { key: "rfq_spread", label: "RFQ vs CEX" },
  { key: "premium_bps", label: "CEX vs Und" },
  { key: "amm_premium", label: "DEX vs Und" },
  { key: "underlying_price", label: "Underlying" },
  { key: "cex_volume_24h", label: "CEX Vol" },
  { key: "tvl_usd", label: "TVL" },
  { key: "dex_volume_24h", label: "DEX Vol" },
  { key: "volume_ratio", label: "CEX/DEX" },
  // WHI-824: USD = max extractable $; bps = size-normalized efficiency.
  { key: "pnl_optimal_usd", label: "Bucket PnL ($)" },
  { key: "pnl_optimal_bps", label: "Bucket PnL (bps)" },
  { key: "bybit_mid", label: "Bybit mid" },
  { key: "pair_id", label: "Pair" },
];

export const SORT_KEY_SET: ReadonlySet<SortKey> = new Set(
  SORT_KEYS.map((s) => s.key),
);

export function isSortKey(value: string): value is SortKey {
  return SORT_KEY_SET.has(value as SortKey);
}

/** Default direction when switching to a column (pair_id asc; metrics desc). */
export function defaultSortDesc(key: SortKey): boolean {
  return key !== "pair_id";
}

// ---------------------------------------------------------------------------
// Top-N overview view (WHI-791 + WHI-796 DEX-tradeable seats)
// ---------------------------------------------------------------------------

/** Default collapsed row budget for the overview table. */
export const TOP_N_DEFAULT = 10;

/** True when the row has a non-null value for the active sort key. */
export function hasSortValue(row: PairOverviewRow, key: SortKey): boolean {
  return rawValue(row, key) !== null;
}

/**
 * DEX-tradeable seat eligibility for Top-N (WHI-796 + WHI-822).
 *
 * A seat requires a live DEX leg — either:
 * - **AMM path:** quotable AMM mid (WHI-795: `amm_mid != null`) **and**
 *   `!low_liquidity` (TVL ≥ inventory `low_liquidity_threshold_usd`; dex:none
 *   always low) **and** no `pricing_anomaly` reason (WHI-822: extreme
 *   |vs CEX| mid is visible but not a tradable claim), or
 * - **RFQ path:** two-sided RFQ quote with positive prices — covers Fluxion
 *   RFQ-only inventory pairs (AMZNx/COINx/MCDx) where `amm is null` and the
 *   inventory low-liq bit would otherwise exclude them.
 *
 * Fewer than N seats is intentional when the chain only has a handful of
 * live pools; do not pad with no_pool / empty_pool / dust / anomaly rows.
 */
export function isDexTradeable(row: PairOverviewRow): boolean {
  // amm_quote_reason is authoritative; pnl_v2.status is belt-and-braces when
  // only the PnL payload is present (same guard populates both on API rows).
  // stale = no live CEX book → cannot verify |vs CEX| (WHI-822); deny AMM seat.
  const ammOk =
    row.amm_mid != null &&
    !row.low_liquidity &&
    !row.stale &&
    row.amm_quote_reason == null &&
    row.pnl_v2?.status !== "pricing_anomaly";
  const rfqBuy = parseNum(row.rfq_buy);
  const rfqSell = parseNum(row.rfq_sell);
  const rfqOk =
    rfqBuy != null && rfqBuy > 0 && rfqSell != null && rfqSell > 0;
  return ammOk || rfqOk;
}

/**
 * Structured reason for a non-tradeable row (Show-all badge).
 * Null only when {@link isDexTradeable} is true.
 */
export function dexNonTradeableReason(
  row: PairOverviewRow,
): DexNonTradeableReason | null {
  if (isDexTradeable(row)) return null;
  // Prefer explicit AMM suppression codes (WHI-795 / WHI-822) over coarser signals.
  if (row.amm_quote_reason === "empty_pool") return "empty_pool";
  if (row.amm_quote_reason === "invalid_mid") return "invalid_mid";
  if (row.amm_quote_reason === "pricing_anomaly") return "pricing_anomaly";
  // Quotable mid but under the TVL floor.
  if (row.amm_mid != null && row.low_liquidity) return "low_liq";
  // PnL snapshot codes when present (more precise than inventory heuristics).
  if (row.pnl_v2?.status === "empty_pool") return "empty_pool";
  if (row.pnl_v2?.status === "invalid_mid") return "invalid_mid";
  if (row.pnl_v2?.status === "pricing_anomaly") return "pricing_anomaly";
  if (row.pnl_v2?.status === "no_pool") return "no_pool";
  // dex:none inventory is always low_liquidity with no mid.
  if (row.low_liquidity && row.amm_mid == null) return "no_pool";
  // Real pool expected (not low-liq) but mid not yet / temporarily missing.
  return "no_quote";
}

export type TopNView = {
  rows: PairOverviewRow[];
  /**
   * Rows that are DEX-tradeable **and** have a non-null sort value
   * (eligible for a collapsed Top-N seat).
   */
  presentCount: number;
  /** Rows that pass {@link isDexTradeable} (after filter + sort). */
  tradeableCount: number;
  /** Full filtered list length (including non-tradeable + n/a). */
  totalCount: number;
  /** How many rows are currently rendered. */
  shownCount: number;
  showAll: boolean;
  /**
   * True when the collapsed view omits rows (eligible > N and/or
   * non-tradeable / trailing n/a exist). Drives the "Show all" footer.
   */
  isTruncated: boolean;
};

/**
 * Apply the Top-N window to an already-sorted row list.
 *
 * Contract (WHI-791 + WHI-796):
 * - Collapsed: only **DEX-tradeable** rows with a present sort value, first
 *   `n` of them. Null / n/a sort values trail (via {@link sortRows}) and never
 *   fill slots; no_pool / empty_pool / low-liq dust never take seats even when
 *   the sort column (e.g. CEX Vol) is populated.
 * - Expanded (`showAll`): full sorted list, including non-tradeable rows
 *   (status badges are a render concern).
 * - When the tradeable set is smaller than `n`, show fewer rows — never pad.
 */
export function applyTopN(
  sortedRows: ReadonlyArray<PairOverviewRow>,
  key: SortKey,
  opts: { n?: number; showAll: boolean },
): TopNView {
  const n = opts.n ?? TOP_N_DEFAULT;
  const totalCount = sortedRows.length;
  const tradeable = sortedRows.filter((r) => isDexTradeable(r));
  const tradeableCount = tradeable.length;
  // Eligible seats: tradeable ∩ has sort value (preserves sort order).
  const eligible = tradeable.filter((r) => hasSortValue(r, key));
  const presentCount = eligible.length;

  if (opts.showAll) {
    return {
      rows: [...sortedRows],
      presentCount,
      tradeableCount,
      totalCount,
      shownCount: totalCount,
      showAll: true,
      isTruncated: false,
    };
  }

  const rows = eligible.slice(0, n);
  return {
    rows,
    presentCount,
    tradeableCount,
    totalCount,
    shownCount: rows.length,
    showAll: false,
    // Collapsed window is shorter than the full filtered list.
    isTruncated: totalCount > rows.length,
  };
}

/** Human label for a sort key (footer / controls). */
export function sortKeyLabel(key: SortKey): string {
  return SORT_KEYS.find((s) => s.key === key)?.label ?? key;
}

/** True when the active sort key is a PnL optimal column (WHI-824). */
export function isPnlSortKey(key: SortKey): boolean {
  return key === "pnl_optimal_usd" || key === "pnl_optimal_bps";
}

/**
 * Compact header label while a PnL sort is active (keeps column width stable
 * enough; full labels live in SORT_KEYS for the sort menu / footer).
 */
export function bucketPnlHeaderLabel(sortKey: SortKey): string {
  if (sortKey === "pnl_optimal_bps") return "Bucket PnL bps";
  if (sortKey === "pnl_optimal_usd") return "Bucket PnL $";
  return "Bucket PnL";
}

/**
 * Header tooltip for Bucket PnL — names the active unit so USD vs bps
 * cannot be confused (optimal notional diverges widely across pairs).
 * Column click cycles: USD↓ → USD↑ → bps↓ → bps↑ → USD↓.
 */
export function bucketPnlSortTitle(sortKey: SortKey): string {
  if (sortKey === "pnl_optimal_bps") {
    return (
      "Sorting by optimal net PnL in bps (size-normalized efficiency). " +
      "Not max extractable USD — pair notionals differ. " +
      "Click again to flip direction, then return to USD. " +
      "Most pairs are negative after costs; desc = highest efficiency, not free money."
    );
  }
  if (sortKey === "pnl_optimal_usd") {
    return (
      "Sorting by optimal net PnL in USD (max extractable $ at Q*). " +
      "Click again: flip direction, then switch to bps efficiency. " +
      "Most pairs are negative after costs; desc = least loss, not a free opportunity."
    );
  }
  return (
    "Click to sort by optimal size net PnL in USD. " +
    "Further clicks cycle USD direction, then bps. Sort menu jumps to either unit."
  );
}

/**
 * Advance Bucket PnL header-click cycle (WHI-824):
 * usd↓ → usd↑ → bps↓ → bps↑ → usd↓.
 * Call only when the column is already on a PnL key.
 */
export function nextPnlSortState(
  sortKey: SortKey,
  sortDesc: boolean,
): { key: SortKey; desc: boolean } {
  if (sortKey === "pnl_optimal_usd" && sortDesc) {
    return { key: "pnl_optimal_usd", desc: false };
  }
  if (sortKey === "pnl_optimal_usd" && !sortDesc) {
    return { key: "pnl_optimal_bps", desc: true };
  }
  if (sortKey === "pnl_optimal_bps" && sortDesc) {
    return { key: "pnl_optimal_bps", desc: false };
  }
  return { key: "pnl_optimal_usd", desc: true };
}

/**
 * Footer count copy — full filtered universe + honest tradeable size so a
 * Top-N window is never mistaken for a 10-pair inventory (WHI-791/796).
 * When sorting PnL descending, note that the board is often "least loss".
 */
export function topNSummary(
  view: TopNView,
  key: SortKey,
  opts?: { sortDesc?: boolean },
): string {
  const label = sortKeyLabel(key);
  const desc = opts?.sortDesc ?? true;
  if (view.showAll) {
    let all =
      `Showing all ${view.totalCount} pairs · sorted by ${label}` +
      ` (${view.tradeableCount} tradeable on DEX)`;
    if (isPnlSortKey(key) && desc) {
      all += " · desc = least loss when all negative";
    }
    return all;
  }
  let msg =
    `Top ${view.shownCount} of ${view.totalCount} by ${label}` +
    ` (${view.tradeableCount} tradeable on DEX)`;
  // When some tradeable rows lack the active sort key, note data coverage.
  if (view.presentCount < view.tradeableCount) {
    msg += ` · ${view.presentCount} with data`;
  }
  if (isPnlSortKey(key) && desc) {
    msg += " · desc = least loss when all negative";
  }
  return msg;
}

// ---------------------------------------------------------------------------
// Shareable overview URL state (WHI-791)
// ---------------------------------------------------------------------------

export type OverviewUrlState = {
  sortKey?: SortKey;
  sortDesc?: boolean;
  showAll?: boolean;
};

/**
 * Parse `?sort=&desc=&all=` from a location search string.
 * Unknown sort keys are ignored (caller keeps server/default).
 * `desc` without a valid `sort` is ignored so a lone `?desc=0` cannot pin
 * the client default sort key and suppress the market API default.
 */
export function parseOverviewSearch(search: string): OverviewUrlState {
  const raw = search.startsWith("?") ? search.slice(1) : search;
  if (!raw) return {};
  const p = new URLSearchParams(raw);
  const out: OverviewUrlState = {};
  const sort = p.get("sort");
  if (sort && isSortKey(sort)) {
    out.sortKey = sort;
    if (p.has("desc")) {
      const d = p.get("desc");
      out.sortDesc = d !== "0" && d !== "false";
    }
  }
  if (p.get("all") === "1" || p.get("all") === "true") {
    out.showAll = true;
  } else if (p.has("all")) {
    out.showAll = false;
  }
  return out;
}

/** Build `?sort=…&desc=…[&all=1]` for replaceState / share links. */
export function buildOverviewSearch(state: {
  sortKey: SortKey;
  sortDesc: boolean;
  showAll: boolean;
}): string {
  const p = new URLSearchParams();
  p.set("sort", state.sortKey);
  p.set("desc", state.sortDesc ? "1" : "0");
  if (state.showAll) {
    p.set("all", "1");
  }
  return `?${p.toString()}`;
}
