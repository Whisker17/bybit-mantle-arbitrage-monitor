/**
 * Overview pair-id badges (WHI-781): high DEX pool TVL and/or high CEX volume.
 *
 * Pure ranking helpers — no React. Badges label *why a listed pair is
 * interesting* along two independent dimensions; they do **not** mean the
 * inventory was rebuilt to CEX volume top-10 (see m7-bstocks-inventory.md).
 *
 * Defaults (product choice; WHI-781 + WHI-782):
 * - both markets: TVL + Vol, top-5 in-market each
 * - TVL rank prefers live `tvl_usd`, else inventory `est_liquidity_usd`
 *
 * Rank on the **full market overview set**, not a UI-filtered subset.
 */

import { parseNum } from "./format";
import type { PairOverviewRow } from "./types";

/** Short labels next to pair id (same density as `stale` / `mm`). */
export const BADGE_LABEL_TVL = "TVL";
export const BADGE_LABEL_VOL = "Vol";

export type PairBadgeFlags = {
  /** High inventory DEX pool liquidity rank. */
  hiTvl: boolean;
  /** High in-market CEX 24h quote volume rank. */
  hiVol: boolean;
};

export type BadgePolicy = {
  /** Rank by inventory `est_liquidity_usd` when true. */
  enableTvl: boolean;
  /** Rank by live/API `cex_volume_24h` when true. */
  enableVol: boolean;
  /** Inclusive rank threshold (1..topN get the badge). */
  topN: number;
  /** Tooltip when TVL badge is shown. */
  tvlTitle: string;
  /** Tooltip when Vol badge is shown. */
  volTitle: string;
};

/** Product threshold for WHI-781 (not in config/ — UI-only rank cut). */
const DEFAULT_TOP_N = 5;

const VOL_TITLE =
  "CEX 24h quote volume rank among this market's pairs (live journal/API) — top 5";

/**
 * Per-market badge policy. Pair *lists* are never hardcoded — ranks come from
 * overview row fields (`tvl_usd` live preferred, else `est_liquidity_usd`,
 * plus `cex_volume_24h`). WHI-782: both markets enable TVL once live journal
 * samples exist; inventory est is the cold-start fallback only.
 */
export function badgePolicyForMarket(_marketId: string): BadgePolicy {
  // Both markets share TVL+Vol top-N after WHI-782 live TVL (inventory est is
  // cold-start fallback only). marketId kept for call-site symmetry / future
  // per-market rank cuts.
  return {
    enableTvl: true,
    enableVol: true,
    topN: DEFAULT_TOP_N,
    tvlTitle:
      "DEX pool TVL rank (live balanceOf when available; else inventory est) — top 5",
    volTitle: VOL_TITLE,
  };
}

/** Prefer live TVL; fall back to inventory est for cold start (WHI-782). */
export function tvlRankValue(row: PairOverviewRow): number | null {
  const live = parseNum(row.tvl_usd);
  if (live != null) return live;
  return parseNum(row.est_liquidity_usd);
}

/**
 * Rank ids by numeric value descending; null/NaN excluded.
 * Ties: higher value first, then pair_id ascending for stable order.
 * Returns the set of ids with rank ≤ topN (1-based).
 */
export function topRankedIds(
  items: ReadonlyArray<{ id: string; value: number | null | undefined }>,
  topN: number,
): Set<string> {
  if (topN <= 0) return new Set();
  const scored = items
    .map((it) => {
      const v = it.value;
      if (v == null || !Number.isFinite(v)) return null;
      return { id: it.id, value: v };
    })
    .filter((x): x is { id: string; value: number } => x != null);
  scored.sort((a, b) => {
    if (b.value !== a.value) return b.value - a.value;
    return a.id.localeCompare(b.id);
  });
  return new Set(scored.slice(0, topN).map((s) => s.id));
}

/**
 * Compute TVL/Vol badge flags for every overview row under a market policy.
 *
 * @param rows Full market overview rows (not a UI-filtered subset).
 * @param policy From {@link badgePolicyForMarket} — pass explicitly so rank
 *   rules cannot drift from the tooltip policy used at render time.
 */
export function pairBadgesForRows(
  rows: ReadonlyArray<PairOverviewRow>,
  policy: BadgePolicy,
): Map<string, PairBadgeFlags> {
  const tvlIds = policy.enableTvl
    ? topRankedIds(
        rows.map((r) => ({
          id: r.pair_id,
          value: tvlRankValue(r),
        })),
        policy.topN,
      )
    : new Set<string>();
  const volIds = policy.enableVol
    ? topRankedIds(
        rows.map((r) => ({
          id: r.pair_id,
          value: parseNum(r.cex_volume_24h),
        })),
        policy.topN,
      )
    : new Set<string>();

  const out = new Map<string, PairBadgeFlags>();
  for (const row of rows) {
    out.set(row.pair_id, {
      hiTvl: tvlIds.has(row.pair_id),
      hiVol: volIds.has(row.pair_id),
    });
  }
  return out;
}
