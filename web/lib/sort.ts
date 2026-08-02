import { cexPremiumBps, parseNum } from "./format";
import type { PairOverviewRow, SortKey } from "./types";

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
  const present = rows.filter((r) => rawValue(r, key) !== null);
  const missing = rows.filter((r) => rawValue(r, key) === null);

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
// Top-N overview view (WHI-791)
// ---------------------------------------------------------------------------

/** Default collapsed row budget for the overview table. */
export const TOP_N_DEFAULT = 10;

/** True when the row has a non-null value for the active sort key. */
export function hasSortValue(row: PairOverviewRow, key: SortKey): boolean {
  return rawValue(row, key) !== null;
}

export type TopNView = {
  rows: PairOverviewRow[];
  /** Rows with a non-null sort value (after filter + sort). */
  presentCount: number;
  /** Full filtered list length (including n/a for the sort key). */
  totalCount: number;
  /** How many rows are currently rendered. */
  shownCount: number;
  /** Cap used when collapsed. */
  topN: number;
  showAll: boolean;
  /**
   * True when the collapsed view omits rows (present > N and/or trailing n/a
   * exist). Drives the "Show all" footer affordance.
   */
  isTruncated: boolean;
};

/**
 * Apply the Top-N window to an already-sorted row list.
 *
 * Contract (WHI-791):
 * - Collapsed: only rows with a present sort value, first `n` of them.
 *   Null / n/a rows sort last (via {@link sortRows}) and **never** fill Top-N
 *   slots (e.g. dex:none pairs with no TVL do not appear in "Top 10 by TVL").
 * - Expanded (`showAll`): full sorted list, trailing n/a included.
 */
export function applyTopN(
  sortedRows: ReadonlyArray<PairOverviewRow>,
  key: SortKey,
  opts: { n?: number; showAll: boolean },
): TopNView {
  const n = opts.n ?? TOP_N_DEFAULT;
  const totalCount = sortedRows.length;
  const present = sortedRows.filter((r) => hasSortValue(r, key));
  const presentCount = present.length;

  if (opts.showAll) {
    return {
      rows: [...sortedRows],
      presentCount,
      totalCount,
      shownCount: totalCount,
      topN: n,
      showAll: true,
      isTruncated: false,
    };
  }

  const rows = present.slice(0, n);
  return {
    rows,
    presentCount,
    totalCount,
    shownCount: rows.length,
    topN: n,
    showAll: false,
    // Collapsed window is shorter than the full filtered list.
    isTruncated: totalCount > rows.length,
  };
}

/** Human label for a sort key (footer / controls). */
export function sortKeyLabel(key: SortKey): string {
  return SORT_KEYS.find((s) => s.key === key)?.label ?? key;
}

/**
 * Footer count copy — must reflect the full filtered universe so a Top-10
 * window is never mistaken for a 10-pair inventory.
 */
export function topNSummary(view: TopNView, key: SortKey): string {
  const label = sortKeyLabel(key);
  if (view.showAll) {
    return `Showing all ${view.totalCount} pairs · sorted by ${label}`;
  }
  let msg = `Top ${view.shownCount} of ${view.totalCount} by ${label}`;
  if (view.presentCount < view.totalCount) {
    msg += ` · ${view.presentCount} with data`;
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
 */
export function parseOverviewSearch(search: string): OverviewUrlState {
  const raw = search.startsWith("?") ? search.slice(1) : search;
  if (!raw) return {};
  const p = new URLSearchParams(raw);
  const out: OverviewUrlState = {};
  const sort = p.get("sort");
  if (sort && isSortKey(sort)) {
    out.sortKey = sort;
  }
  if (p.has("desc")) {
    const d = p.get("desc");
    out.sortDesc = d !== "0" && d !== "false";
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
