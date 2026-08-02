/**
 * Overview pair-id badges (WHI-781): high DEX pool TVL and/or high CEX volume.
 *
 * Pure ranking helpers — no React. Badges label *why a listed pair is
 * interesting* along two independent dimensions; they do **not** mean the
 * inventory was rebuilt to CEX volume top-10 (see m7-bstocks-inventory.md).
 *
 * Defaults (resolve in PR if product adjusts):
 * - binance-pancake: TVL + Vol, top-5 in-market each
 * - bybit-fluxion: Vol only (no PCS TVL inventory story), top-5
 */

import type { PairOverviewRow } from "./types";

/** Short labels next to pair id (same density as `stale` / `mm`). */
export const BADGE_LABEL_TVL = "TVL";
export const BADGE_LABEL_VOL = "Vol";

export type PairBadgeFlags = {
  /** High inventory DEX pool liquidity rank. */
  hiTvl: boolean;
  /** High in-market CEX 24h quote volume rank. */
  hiVol: boolean;
  tvlTitle: string;
  volTitle: string;
};

export type BadgePolicy = {
  /** Rank by inventory `est_liquidity_usd` when true. */
  enableTvl: boolean;
  /** Rank by live/API `cex_volume_24h` when true. */
  enableVol: boolean;
  /** Inclusive rank threshold (1..topN get the badge). */
  topN: number;
  tvlTitle: string;
  volTitle: string;
};

const DEFAULT_TOP_N = 5;

/**
 * Per-market badge policy. Market-id branching is intentional; pair *lists*
 * are never hardcoded — ranks come from overview row fields.
 */
export function badgePolicyForMarket(marketId: string): BadgePolicy {
  if (marketId === "binance-pancake") {
    return {
      enableTvl: true,
      enableVol: true,
      topN: DEFAULT_TOP_N,
      tvlTitle:
        "PCS USDT pool liquidity rank (inventory snapshot) — top half of this market",
      volTitle:
        "CEX 24h quote volume rank among this market's pairs (live journal/API)",
    };
  }
  // bybit-fluxion (default): no Fluxion TVL ranking story for the badge.
  return {
    enableTvl: false,
    enableVol: true,
    topN: DEFAULT_TOP_N,
    tvlTitle: "DEX pool liquidity rank (not used on this market)",
    volTitle:
      "CEX 24h quote volume rank among this market's pairs (live journal/API)",
  };
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

function parseUsd(raw: string | null | undefined): number | null {
  if (raw == null || raw === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/**
 * Compute TVL/Vol badge flags for every overview row under a market policy.
 */
export function pairBadgesForRows(
  rows: ReadonlyArray<PairOverviewRow>,
  marketId: string,
  policy: BadgePolicy = badgePolicyForMarket(marketId),
): Map<string, PairBadgeFlags> {
  const tvlIds = policy.enableTvl
    ? topRankedIds(
        rows.map((r) => ({
          id: r.pair_id,
          value: parseUsd(r.est_liquidity_usd),
        })),
        policy.topN,
      )
    : new Set<string>();
  const volIds = policy.enableVol
    ? topRankedIds(
        rows.map((r) => ({
          id: r.pair_id,
          value: parseUsd(r.cex_volume_24h),
        })),
        policy.topN,
      )
    : new Set<string>();

  const out = new Map<string, PairBadgeFlags>();
  for (const row of rows) {
    out.set(row.pair_id, {
      hiTvl: tvlIds.has(row.pair_id),
      hiVol: volIds.has(row.pair_id),
      tvlTitle: policy.tvlTitle,
      volTitle: policy.volTitle,
    });
  }
  return out;
}
