import { parseNum } from "./format";
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
  { key: "amm_spread", label: "AMM bps" },
  { key: "rfq_spread", label: "RFQ bps" },
  { key: "cex_volume_24h", label: "CEX Vol" },
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
