import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  BADGE_LABEL_TVL,
  BADGE_LABEL_VOL,
  badgePolicyForMarket,
  pairBadgesForRows,
  topRankedIds,
} from "./pair-badges";
import type { PairOverviewRow } from "./types";

function row(
  partial: Partial<PairOverviewRow> & { pair_id: string },
): PairOverviewRow {
  return {
    name: partial.pair_id,
    low_liquidity: false,
    session: "open",
    bybit_bid: null,
    bybit_ask: null,
    bybit_mid: null,
    amm_mid: null,
    rfq_buy: null,
    rfq_sell: null,
    amm_spread_bps: null,
    rfq_spread_bps: null,
    net_edge_bps: null,
    net_edge_venue: null,
    net_edge_direction: null,
    reference_size_usd: "1000",
    volume_24h: "0",
    trades_24h: 0,
    stale: false,
    ...partial,
  };
}

describe("topRankedIds", () => {
  it("takes top N by value desc and excludes nulls", () => {
    const ids = topRankedIds(
      [
        { id: "a", value: 10 },
        { id: "b", value: null },
        { id: "c", value: 50 },
        { id: "d", value: 30 },
        { id: "e", value: 20 },
      ],
      3,
    );
    assert.deepEqual([...ids].sort(), ["c", "d", "e"].sort());
  });

  it("breaks ties by pair_id ascending for stable order", () => {
    const ids = topRankedIds(
      [
        { id: "z", value: 100 },
        { id: "a", value: 100 },
        { id: "m", value: 100 },
      ],
      2,
    );
    // a and m win the tie-break (lexicographic), z is rank 3.
    assert.equal(ids.has("a"), true);
    assert.equal(ids.has("m"), true);
    assert.equal(ids.has("z"), false);
  });

  it("returns empty when topN <= 0 or no scores", () => {
    assert.equal(topRankedIds([{ id: "a", value: 1 }], 0).size, 0);
    assert.equal(topRankedIds([{ id: "a", value: null }], 5).size, 0);
  });
});

describe("badgePolicyForMarket", () => {
  it("enables TVL+Vol on binance-pancake", () => {
    const p = badgePolicyForMarket("binance-pancake");
    assert.equal(p.enableTvl, true);
    assert.equal(p.enableVol, true);
    assert.equal(p.topN, 5);
  });

  it("enables Vol-only on bybit-fluxion", () => {
    const p = badgePolicyForMarket("bybit-fluxion");
    assert.equal(p.enableTvl, false);
    assert.equal(p.enableVol, true);
  });
});

describe("pairBadgesForRows", () => {
  it("marks top-5 TVL and top-5 Vol independently (binance-pancake)", () => {
    // Inventory-like TVL order: SPCXB..MUB; CEX vol order inverted so
    // overlap is only the middle names when topN=5.
    const rows = [
      row({ pair_id: "SPCXB", est_liquidity_usd: "3000000", cex_volume_24h: "100" }),
      row({ pair_id: "SKHYB", est_liquidity_usd: "500000", cex_volume_24h: "200" }),
      row({ pair_id: "TSLAB", est_liquidity_usd: "260000", cex_volume_24h: "300" }),
      row({ pair_id: "SPYB", est_liquidity_usd: "250000", cex_volume_24h: "400" }),
      row({ pair_id: "NVDAB", est_liquidity_usd: "220000", cex_volume_24h: "500" }),
      row({ pair_id: "AAPLB", est_liquidity_usd: "120000", cex_volume_24h: "600" }),
      row({ pair_id: "GOOGLB", est_liquidity_usd: "70000", cex_volume_24h: "700" }),
      row({ pair_id: "MSFTB", est_liquidity_usd: "40000", cex_volume_24h: "800" }),
      row({ pair_id: "INTCB", est_liquidity_usd: "15000", cex_volume_24h: "900" }),
      row({ pair_id: "MUB", est_liquidity_usd: "9000", cex_volume_24h: "1000" }),
    ];
    const bp = badgePolicyForMarket("binance-pancake");
    const badges = pairBadgesForRows(rows, bp);

    // TVL top 5: SPCXB SKHYB TSLAB SPYB NVDAB
    assert.equal(badges.get("SPCXB")?.hiTvl, true);
    assert.equal(badges.get("NVDAB")?.hiTvl, true);
    assert.equal(badges.get("AAPLB")?.hiTvl, false);
    assert.equal(badges.get("MUB")?.hiTvl, false);

    // Vol top 5: MUB INTCB MSFTB GOOGLB AAPLB
    assert.equal(badges.get("MUB")?.hiVol, true);
    assert.equal(badges.get("AAPLB")?.hiVol, true);
    assert.equal(badges.get("NVDAB")?.hiVol, false);
    assert.equal(badges.get("SPCXB")?.hiVol, false);

    // Dual-badge example when a name ranks high on both dimensions.
    const dual = [
      row({ pair_id: "SPCXB", est_liquidity_usd: "3e6", cex_volume_24h: "9e6" }),
      row({ pair_id: "SKHYB", est_liquidity_usd: "5e5", cex_volume_24h: "8e6" }),
      row({ pair_id: "MUB", est_liquidity_usd: "9e3", cex_volume_24h: "7e6" }),
      row({ pair_id: "X", est_liquidity_usd: "1", cex_volume_24h: "1" }),
      row({ pair_id: "Y", est_liquidity_usd: "2", cex_volume_24h: "2" }),
      row({ pair_id: "Z", est_liquidity_usd: "3", cex_volume_24h: "3" }),
    ];
    const dualBadges = pairBadgesForRows(dual, bp);
    assert.equal(dualBadges.get("SPCXB")?.hiTvl, true);
    assert.equal(dualBadges.get("SPCXB")?.hiVol, true);
    assert.equal(dualBadges.get("SKHYB")?.hiTvl, true);
    assert.equal(dualBadges.get("SKHYB")?.hiVol, true);
  });

  it("never sets hiTvl on bybit-fluxion", () => {
    const rows = [
      row({ pair_id: "AAPLx", est_liquidity_usd: "100000", cex_volume_24h: "5000" }),
      row({ pair_id: "TSLAx", est_liquidity_usd: "90000", cex_volume_24h: "4000" }),
    ];
    const badges = pairBadgesForRows(rows, badgePolicyForMarket("bybit-fluxion"));
    assert.equal(badges.get("AAPLx")?.hiTvl, false);
    assert.equal(badges.get("AAPLx")?.hiVol, true);
    assert.equal(badges.get("TSLAx")?.hiVol, true);
  });

  it("ranks on the provided full set (caller must pass unfiltered rows)", () => {
    // Simulates market top-5 TVL: only A is top when ranking the full set.
    const full = [
      row({ pair_id: "A", est_liquidity_usd: "1000", cex_volume_24h: "1" }),
      row({ pair_id: "B", est_liquidity_usd: "100", cex_volume_24h: "2" }),
      row({ pair_id: "C", est_liquidity_usd: "50", cex_volume_24h: "3" }),
      row({ pair_id: "D", est_liquidity_usd: "40", cex_volume_24h: "4" }),
      row({ pair_id: "E", est_liquidity_usd: "30", cex_volume_24h: "5" }),
      row({ pair_id: "F", est_liquidity_usd: "20", cex_volume_24h: "6" }),
    ];
    const bp = badgePolicyForMarket("binance-pancake");
    const onFull = pairBadgesForRows(full, bp);
    assert.equal(onFull.get("A")?.hiTvl, true);
    assert.equal(onFull.get("F")?.hiTvl, false);
    // If a caller wrongly ranks only the filtered subset, F becomes top-1.
    const filteredOnly = pairBadgesForRows([full[5]!], bp);
    assert.equal(filteredOnly.get("F")?.hiTvl, true);
  });

  it("exports short English labels", () => {
    assert.equal(BADGE_LABEL_TVL, "TVL");
    assert.equal(BADGE_LABEL_VOL, "Vol");
  });
});
