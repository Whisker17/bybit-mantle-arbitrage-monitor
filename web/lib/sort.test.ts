import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  applyTopN,
  buildOverviewSearch,
  bucketPnlHeaderLabel,
  bucketPnlSortTitle,
  dexNonTradeableReason,
  filterRows,
  isDexTradeable,
  isPnlSortKey,
  isSortKey,
  nextPnlSortState,
  parseOverviewSearch,
  sortKeyLabel,
  sortRows,
  topNSummary,
  TOP_N_DEFAULT,
} from "./sort";
import type { PairOverviewRow, PnlOptimalSummary } from "./types";

function row(partial: Partial<PairOverviewRow> & { pair_id: string }): PairOverviewRow {
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
    net_size_usd: null,
    reference_size_usd: "1000",
    volume_24h: "0",
    trades_24h: 0,
    stale: false,
    ...partial,
  };
}

describe("sortRows", () => {
  it("sorts numeric keys desc and parks nulls last", () => {
    const rows = [
      row({ pair_id: "A", net_edge_bps: "1" }),
      row({ pair_id: "B", net_edge_bps: null }),
      row({ pair_id: "C", net_edge_bps: "10" }),
    ];
    const sorted = sortRows(rows, "net_edge", true);
    assert.deepEqual(
      sorted.map((r) => r.pair_id),
      ["C", "A", "B"],
    );
  });

  it("sorts CEX vs Und via premium_bps key with cex alias (WHI-783)", () => {
    const rows = [
      row({ pair_id: "A", premium_bps: "10" }),
      row({ pair_id: "B", cex_premium_bps: "30" }),
      row({ pair_id: "C", cex_premium_bps: "5", premium_bps: "99" }),
      row({ pair_id: "D" }),
    ];
    // cex field wins over legacy premium_bps; nulls last.
    const sorted = sortRows(rows, "premium_bps", true);
    assert.deepEqual(
      sorted.map((r) => r.pair_id),
      ["B", "A", "C", "D"],
    );
  });

  it("sorts DEX vs Und via amm_premium key", () => {
    const rows = [
      row({ pair_id: "A", amm_premium_bps: "-10" }),
      row({ pair_id: "B", amm_premium_bps: "20" }),
      row({ pair_id: "C" }),
    ];
    const sorted = sortRows(rows, "amm_premium", true);
    assert.deepEqual(
      sorted.map((r) => r.pair_id),
      ["B", "A", "C"],
    );
  });

  it("sorts pnl_optimal_usd desc; non-ok status nulls last (WHI-824)", () => {
    const rows = [
      row({
        pair_id: "neg_big",
        pnl_optimal_net_usd: "-5",
        pnl_optimal_net_bps: "-50",
        pnl_v2: okPnl({ optimal_net_pnl_usd: "-5", optimal_net_pnl_bps: "-50" }),
      }),
      row({
        pair_id: "pos",
        pnl_optimal_net_usd: "2",
        pnl_optimal_net_bps: "5",
        pnl_v2: okPnl({ optimal_net_pnl_usd: "2", optimal_net_pnl_bps: "5" }),
      }),
      row({
        pair_id: "no_depth",
        pnl_v2: {
          status: "no_depth",
          has_depth: false,
          direction: null,
          optimal_notional_usd: null,
          optimal_net_pnl_usd: null,
          optimal_net_pnl_bps: null,
          bybit_depth_source: null,
        },
      }),
      row({
        pair_id: "neg_small",
        pnl_optimal_net_usd: "-0.5",
        pnl_optimal_net_bps: "-100",
        pnl_v2: okPnl({
          optimal_net_pnl_usd: "-0.5",
          optimal_net_pnl_bps: "-100",
        }),
      }),
    ];
    const sorted = sortRows(rows, "pnl_optimal_usd", true);
    assert.deepEqual(
      sorted.map((r) => r.pair_id),
      ["pos", "neg_small", "neg_big", "no_depth"],
    );
  });

  it("sorts pnl_optimal_bps independently of USD (WHI-824)", () => {
    const rows = [
      row({
        pair_id: "big_usd",
        pnl_optimal_net_usd: "10",
        pnl_optimal_net_bps: "5",
      }),
      row({
        pair_id: "high_bps",
        pnl_optimal_net_usd: "1",
        pnl_optimal_net_bps: "50",
      }),
      row({ pair_id: "missing" }),
    ];
    assert.deepEqual(
      sortRows(rows, "pnl_optimal_usd", true).map((r) => r.pair_id),
      ["big_usd", "high_bps", "missing"],
    );
    assert.deepEqual(
      sortRows(rows, "pnl_optimal_bps", true).map((r) => r.pair_id),
      ["high_bps", "big_usd", "missing"],
    );
  });

  it("falls back to nested pnl_v2 when flat fields absent (WHI-824)", () => {
    const rows = [
      row({
        pair_id: "A",
        pnl_v2: okPnl({ optimal_net_pnl_usd: "1", optimal_net_pnl_bps: "10" }),
      }),
      row({
        pair_id: "B",
        pnl_v2: okPnl({ optimal_net_pnl_usd: "3", optimal_net_pnl_bps: "5" }),
      }),
      row({
        pair_id: "C",
        pnl_v2: {
          status: "empty_pool",
          has_depth: false,
          direction: null,
          optimal_notional_usd: null,
          optimal_net_pnl_usd: null,
          optimal_net_pnl_bps: null,
          bybit_depth_source: null,
        },
      }),
    ];
    assert.deepEqual(
      sortRows(rows, "pnl_optimal_usd", true).map((r) => r.pair_id),
      ["B", "A", "C"],
    );
  });

  it("negative-only USD desc is least loss first (WHI-824)", () => {
    const rows = [
      row({ pair_id: "worst", pnl_optimal_net_usd: "-20" }),
      row({ pair_id: "least", pnl_optimal_net_usd: "-1" }),
      row({ pair_id: "mid", pnl_optimal_net_usd: "-8" }),
    ];
    assert.deepEqual(
      sortRows(rows, "pnl_optimal_usd", true).map((r) => r.pair_id),
      ["least", "mid", "worst"],
    );
  });
});

function okPnl(
  partial: Partial<PnlOptimalSummary> & {
    optimal_net_pnl_usd: string;
    optimal_net_pnl_bps: string;
  },
): PnlOptimalSummary {
  return {
    status: "ok",
    has_depth: true,
    direction: "buy_fluxion_sell_bybit",
    optimal_notional_usd: "1000",
    bybit_depth_source: "book",
    ...partial,
  };
}

describe("filterRows", () => {
  it("filters by query and flags", () => {
    const rows = [
      row({ pair_id: "AAPLx", name: "Apple", low_liquidity: false }),
      row({ pair_id: "MCDx", name: "McD", low_liquidity: true }),
      row({ pair_id: "TSLAx", name: "Tesla", stale: true }),
    ];
    assert.equal(filterRows(rows, { query: "aap", hideLowLiquidity: false, hideStale: false }).length, 1);
    assert.equal(filterRows(rows, { query: "", hideLowLiquidity: true, hideStale: false }).length, 2);
    assert.equal(filterRows(rows, { query: "", hideLowLiquidity: false, hideStale: true }).length, 2);
  });
});

describe("isDexTradeable (WHI-796)", () => {
  it("AMM path: requires quotable mid and not low_liquidity", () => {
    assert.equal(
      isDexTradeable(row({ pair_id: "OK", amm_mid: "100", low_liquidity: false })),
      true,
    );
    // Empty / invalid mid suppressed by WHI-795 → not tradeable.
    assert.equal(
      isDexTradeable(
        row({
          pair_id: "EMPTY",
          amm_mid: null,
          amm_quote_reason: "empty_pool",
          low_liquidity: true,
        }),
      ),
      false,
    );
    // dex:none / no pool: no mid, low_liquidity true.
    assert.equal(
      isDexTradeable(row({ pair_id: "NONE", amm_mid: null, low_liquidity: true })),
      false,
    );
    // Sub-threshold TVL: mid may exist but floor fails.
    assert.equal(
      isDexTradeable(row({ pair_id: "DUST", amm_mid: "10", low_liquidity: true })),
      false,
    );
    // Cold-start: inventory not low but mid not yet — no seat until quotable.
    assert.equal(
      isDexTradeable(row({ pair_id: "WAIT", amm_mid: null, low_liquidity: false })),
      false,
    );
    // WHI-822: mid present but |vs CEX| anomaly — not a tradable seat.
    assert.equal(
      isDexTradeable(
        row({
          pair_id: "SPYB",
          amm_mid: "836.50",
          low_liquidity: false,
          amm_quote_reason: "pricing_anomaly",
        }),
      ),
      false,
    );
    // WHI-822: no CEX book (stale) — cannot verify basis; deny AMM seat.
    assert.equal(
      isDexTradeable(
        row({
          pair_id: "STALE",
          amm_mid: "100",
          low_liquidity: false,
          stale: true,
        }),
      ),
      false,
    );
  });

  it("RFQ path: two-sided RFQ seats even when AMM is null / low_liq", () => {
    // Fluxion RFQ-only inventory (AMZNx-class): amm null, inventory low_liq.
    assert.equal(
      isDexTradeable(
        row({
          pair_id: "AMZNx",
          amm_mid: null,
          low_liquidity: true,
          rfq_buy: "180",
          rfq_sell: "181",
        }),
      ),
      true,
    );
    // One-sided RFQ is not enough.
    assert.equal(
      isDexTradeable(
        row({
          pair_id: "HALF",
          amm_mid: null,
          low_liquidity: true,
          rfq_buy: "180",
          rfq_sell: null,
        }),
      ),
      false,
    );
    // Non-positive / unparseable quotes do not seat.
    assert.equal(
      isDexTradeable(
        row({
          pair_id: "ZERO",
          amm_mid: null,
          low_liquidity: true,
          rfq_buy: "0",
          rfq_sell: "181",
        }),
      ),
      false,
    );
  });
});

describe("applyTopN (WHI-791 + WHI-796)", () => {
  it("defaults TOP_N_DEFAULT to 10", () => {
    assert.equal(TOP_N_DEFAULT, 10);
  });

  it("collapsed: keeps only top N present values; nulls do not fill slots", () => {
    // Already sorted by tvl desc with nulls last (sortRows contract).
    // Tradeable rows need amm_mid + !low_liquidity (WHI-796).
    const sorted = [
      row({ pair_id: "A", tvl_usd: "500", amm_mid: "1", low_liquidity: false }),
      row({ pair_id: "B", tvl_usd: "400", amm_mid: "1", low_liquidity: false }),
      row({ pair_id: "C", tvl_usd: "300", amm_mid: "1", low_liquidity: false }),
      row({ pair_id: "D", tvl_usd: null, amm_mid: null, low_liquidity: true }),
      row({ pair_id: "E", amm_mid: null, low_liquidity: true }), // missing tvl
    ];
    const view = applyTopN(sorted, "tvl_usd", { n: 2, showAll: false });
    assert.deepEqual(
      view.rows.map((r) => r.pair_id),
      ["A", "B"],
    );
    assert.equal(view.presentCount, 3);
    assert.equal(view.tradeableCount, 3);
    assert.equal(view.totalCount, 5);
    assert.equal(view.shownCount, 2);
    assert.equal(view.isTruncated, true);
    assert.equal(view.showAll, false);
  });

  it("collapsed: when fewer than N have values, shows only present (no n/a padding)", () => {
    const sorted = [
      row({
        pair_id: "A",
        cex_volume_24h: "100",
        amm_mid: "1",
        low_liquidity: false,
      }),
      row({
        pair_id: "B",
        cex_volume_24h: null,
        amm_mid: "1",
        low_liquidity: false,
      }),
      row({
        pair_id: "C",
        cex_volume_24h: null,
        amm_mid: null,
        low_liquidity: true,
      }),
    ];
    const view = applyTopN(sorted, "cex_volume_24h", { n: 10, showAll: false });
    assert.deepEqual(
      view.rows.map((r) => r.pair_id),
      ["A"],
    );
    assert.equal(view.shownCount, 1);
    assert.equal(view.tradeableCount, 2);
    // Full list still larger than shown → truncated for "show all" affordance.
    assert.equal(view.isTruncated, true);
  });

  it("collapsed: zero present values → empty rows, still truncated for expand", () => {
    const sorted = [
      row({ pair_id: "A", tvl_usd: null, amm_mid: null, low_liquidity: true }),
      row({ pair_id: "B", amm_mid: null, low_liquidity: true }),
    ];
    const view = applyTopN(sorted, "tvl_usd", { n: 10, showAll: false });
    assert.deepEqual(view.rows, []);
    assert.equal(view.presentCount, 0);
    assert.equal(view.tradeableCount, 0);
    assert.equal(view.totalCount, 2);
    assert.equal(view.isTruncated, true);
  });

  it("collapsed: pricing_anomaly mid never takes Top-N seat (WHI-822)", () => {
    // SPYB-shaped: liquid mid + high TVL + huge |vs CEX| reason must not seat.
    const sorted = [
      row({
        pair_id: "SPYB",
        cex_volume_24h: "9000",
        amm_mid: "836.50",
        amm_spread_bps: "1127.9",
        amm_quote_reason: "pricing_anomaly",
        low_liquidity: false,
        tvl_usd: "227681",
        pnl_v2: {
          status: "pricing_anomaly",
          has_depth: true,
          direction: null,
          optimal_notional_usd: null,
          optimal_net_pnl_usd: null,
          optimal_net_pnl_bps: null,
          bybit_depth_source: null,
        },
      }),
      row({
        pair_id: "QQQB",
        cex_volume_24h: "100",
        amm_mid: "692.51",
        low_liquidity: false,
        tvl_usd: "2000000",
      }),
    ];
    const view = applyTopN(sorted, "cex_volume_24h", { n: 10, showAll: false });
    assert.deepEqual(
      view.rows.map((r) => r.pair_id),
      ["QQQB"],
    );
    assert.equal(view.tradeableCount, 1);
    assert.equal(dexNonTradeableReason(sorted[0]!), "pricing_anomaly");
  });

  it("collapsed: no_pool / empty_pool never take Top-N seats even with CEX Vol (WHI-796)", () => {
    // Sorted by CEX Vol desc — SNXXB/NBISB would win on volume alone but have no pool.
    const sorted = [
      row({
        pair_id: "SNXXB",
        cex_volume_24h: "9000",
        amm_mid: null,
        low_liquidity: true,
        pnl_v2: {
          status: "no_pool",
          has_depth: false,
          direction: null,
          optimal_notional_usd: null,
          optimal_net_pnl_usd: null,
          optimal_net_pnl_bps: null,
          bybit_depth_source: null,
        },
      }),
      row({
        pair_id: "NBISB",
        cex_volume_24h: "8000",
        amm_mid: null,
        low_liquidity: true,
      }),
      row({
        pair_id: "CRCLB",
        cex_volume_24h: "7000",
        amm_mid: null,
        amm_quote_reason: "empty_pool",
        low_liquidity: true,
      }),
      row({
        pair_id: "TSLAB",
        cex_volume_24h: "100",
        amm_mid: "250",
        low_liquidity: false,
        tvl_usd: "200000",
      }),
      row({
        pair_id: "NVDAB",
        cex_volume_24h: "50",
        amm_mid: "100",
        low_liquidity: false,
        tvl_usd: "300000",
      }),
    ];
    const view = applyTopN(sorted, "cex_volume_24h", { n: 10, showAll: false });
    assert.deepEqual(
      view.rows.map((r) => r.pair_id),
      ["TSLAB", "NVDAB"],
    );
    assert.equal(view.shownCount, 2);
    assert.equal(view.tradeableCount, 2);
    assert.equal(view.totalCount, 5);
    // Must not pad to N with unqualified pairs.
    assert.ok(view.shownCount < 10);
  });

  it("collapsed: dust mid (low_liquidity) does not fill Top-N under net_edge sort", () => {
    const sorted = [
      row({
        pair_id: "DUST",
        net_edge_bps: "500",
        amm_mid: "0.01",
        low_liquidity: true,
        tvl_usd: "5",
      }),
      row({
        pair_id: "REAL",
        net_edge_bps: "10",
        amm_mid: "100",
        low_liquidity: false,
        tvl_usd: "200000",
      }),
    ];
    const view = applyTopN(sorted, "net_edge", { n: 10, showAll: false });
    assert.deepEqual(
      view.rows.map((r) => r.pair_id),
      ["REAL"],
    );
  });

  it("showAll: returns full sorted list including non-tradeable + trailing n/a", () => {
    const sorted = [
      row({
        pair_id: "A",
        tvl_usd: "10",
        amm_mid: "1",
        low_liquidity: false,
      }),
      row({
        pair_id: "B",
        tvl_usd: null,
        amm_mid: null,
        low_liquidity: true,
      }),
    ];
    const view = applyTopN(sorted, "tvl_usd", { n: 10, showAll: true });
    assert.deepEqual(
      view.rows.map((r) => r.pair_id),
      ["A", "B"],
    );
    assert.equal(view.tradeableCount, 1);
    assert.equal(view.isTruncated, false);
    assert.equal(view.showAll, true);
  });

  it("works after sortRows on unsorted input (TVL n/a last, not in top N)", () => {
    const rows = [
      row({ pair_id: "NA", tvl_usd: null, amm_mid: null, low_liquidity: true }),
      row({
        pair_id: "LO",
        tvl_usd: "1",
        amm_mid: "1",
        low_liquidity: false,
      }),
      row({
        pair_id: "HI",
        tvl_usd: "99",
        amm_mid: "1",
        low_liquidity: false,
      }),
      row({
        pair_id: "MID",
        tvl_usd: "50",
        amm_mid: "1",
        low_liquidity: false,
      }),
    ];
    const sorted = sortRows(rows, "tvl_usd", true);
    const view = applyTopN(sorted, "tvl_usd", { n: 2, showAll: false });
    assert.deepEqual(
      view.rows.map((r) => r.pair_id),
      ["HI", "MID"],
    );
  });
});

describe("topNSummary / sortKeyLabel (WHI-791 + WHI-796)", () => {
  it("labels known sort keys", () => {
    assert.equal(sortKeyLabel("tvl_usd"), "TVL");
    assert.equal(sortKeyLabel("cex_volume_24h"), "CEX Vol");
  });

  it("reports Top N of total by key with tradeable count when collapsed", () => {
    const sorted = Array.from({ length: 55 }, (_, i) =>
      row({
        pair_id: `P${i}`,
        tvl_usd: String(1000 - i),
        amm_mid: "1",
        low_liquidity: false,
      }),
    );
    const view = applyTopN(sorted, "tvl_usd", { n: 10, showAll: false });
    assert.equal(
      topNSummary(view, "tvl_usd"),
      "Top 10 of 55 by TVL (55 tradeable on DEX)",
    );
  });

  it("honest count when tradeable set is smaller than N (no padding)", () => {
    // 55 universe, only 2 tradeable — must not invent 10 seats.
    const sorted = [
      row({
        pair_id: "TSLAB",
        cex_volume_24h: "100",
        amm_mid: "1",
        low_liquidity: false,
      }),
      row({
        pair_id: "NVDAB",
        cex_volume_24h: "50",
        amm_mid: "1",
        low_liquidity: false,
      }),
      ...Array.from({ length: 53 }, (_, i) =>
        row({
          pair_id: `X${i}`,
          cex_volume_24h: String(1000 + i),
          amm_mid: null,
          low_liquidity: true,
        }),
      ),
    ];
    // Pre-sorted by CEX vol would put X* first; simulate post-sort order with
    // high CEX vol non-tradeable first, then tradeable.
    const view = applyTopN(sorted, "cex_volume_24h", { n: 10, showAll: false });
    assert.deepEqual(
      view.rows.map((r) => r.pair_id),
      ["TSLAB", "NVDAB"],
    );
    assert.equal(
      topNSummary(view, "cex_volume_24h"),
      "Top 2 of 55 by CEX Vol (2 tradeable on DEX)",
    );
  });

  it("notes how many eligible rows have sort data when some tradeable lack it", () => {
    const sorted = [
      row({
        pair_id: "A",
        tvl_usd: "10",
        amm_mid: "1",
        low_liquidity: false,
      }),
      row({
        pair_id: "B",
        tvl_usd: null,
        amm_mid: "1",
        low_liquidity: false,
      }),
      row({
        pair_id: "C",
        tvl_usd: null,
        amm_mid: null,
        low_liquidity: true,
      }),
    ];
    const view = applyTopN(sorted, "tvl_usd", { n: 10, showAll: false });
    assert.equal(
      topNSummary(view, "tvl_usd"),
      "Top 1 of 3 by TVL (2 tradeable on DEX) · 1 with data",
    );
  });

  it("reports full list when expanded", () => {
    const sorted = [
      row({
        pair_id: "A",
        tvl_usd: "1",
        amm_mid: "1",
        low_liquidity: false,
      }),
      row({
        pair_id: "B",
        tvl_usd: "2",
        amm_mid: null,
        low_liquidity: true,
      }),
    ];
    const view = applyTopN(sorted, "tvl_usd", { n: 10, showAll: true });
    assert.equal(
      topNSummary(view, "tvl_usd"),
      "Showing all 2 pairs · sorted by TVL (1 tradeable on DEX)",
    );
  });

  it("collapsed: non-numeric PnL status does not take Top-N seats (WHI-824)", () => {
    // Sorted by pnl_optimal_usd desc with nulls last.
    const sorted = [
      row({
        pair_id: "WIN",
        amm_mid: "100",
        low_liquidity: false,
        pnl_optimal_net_usd: "2",
        pnl_v2: okPnl({ optimal_net_pnl_usd: "2", optimal_net_pnl_bps: "10" }),
      }),
      row({
        pair_id: "LOSS",
        amm_mid: "100",
        low_liquidity: false,
        pnl_optimal_net_usd: "-1",
        pnl_v2: okPnl({
          optimal_net_pnl_usd: "-1",
          optimal_net_pnl_bps: "-5",
        }),
      }),
      row({
        pair_id: "NO_DEPTH",
        amm_mid: "100",
        low_liquidity: false,
        // tradeable AMM but no PnL number → must not pad Top-N
        pnl_v2: {
          status: "no_depth",
          has_depth: false,
          direction: null,
          optimal_notional_usd: null,
          optimal_net_pnl_usd: null,
          optimal_net_pnl_bps: null,
          bybit_depth_source: null,
        },
      }),
      row({
        pair_id: "NO_POOL",
        amm_mid: null,
        low_liquidity: true,
        pnl_v2: {
          status: "no_pool",
          has_depth: false,
          direction: null,
          optimal_notional_usd: null,
          optimal_net_pnl_usd: null,
          optimal_net_pnl_bps: null,
          bybit_depth_source: null,
        },
      }),
    ];
    const view = applyTopN(sorted, "pnl_optimal_usd", { n: 10, showAll: false });
    assert.deepEqual(
      view.rows.map((r) => r.pair_id),
      ["WIN", "LOSS"],
    );
    assert.equal(view.presentCount, 2);
    assert.equal(view.tradeableCount, 3); // WIN, LOSS, NO_DEPTH
    assert.match(
      topNSummary(view, "pnl_optimal_usd", { sortDesc: true }),
      /Top 2 of 4 by Bucket PnL \(\$\) \(3 tradeable on DEX\) · 2 with data · desc = least loss/,
    );
    // Ascending omits the least-loss note.
    assert.equal(
      topNSummary(view, "pnl_optimal_usd", { sortDesc: false }).includes(
        "least loss",
      ),
      false,
    );
  });
});

describe("PnL sort keys meta (WHI-824)", () => {
  it("accepts both PnL keys in URL parse / isSortKey", () => {
    assert.equal(isSortKey("pnl_optimal_usd"), true);
    assert.equal(isSortKey("pnl_optimal_bps"), true);
    assert.equal(isPnlSortKey("pnl_optimal_usd"), true);
    assert.equal(isPnlSortKey("net_edge"), false);
    const parsed = parseOverviewSearch("?sort=pnl_optimal_bps&desc=1");
    assert.equal(parsed.sortKey, "pnl_optimal_bps");
    assert.equal(parsed.sortDesc, true);
    assert.equal(
      buildOverviewSearch({
        sortKey: "pnl_optimal_usd",
        sortDesc: true,
        showAll: false,
      }),
      "?sort=pnl_optimal_usd&desc=1",
    );
  });

  it("labels and tooltip name the active unit", () => {
    assert.equal(sortKeyLabel("pnl_optimal_usd"), "Bucket PnL ($)");
    assert.equal(sortKeyLabel("pnl_optimal_bps"), "Bucket PnL (bps)");
    assert.equal(bucketPnlHeaderLabel("pnl_optimal_usd"), "Bucket PnL $");
    assert.equal(bucketPnlHeaderLabel("pnl_optimal_bps"), "Bucket PnL bps");
    assert.equal(bucketPnlHeaderLabel("net_edge"), "Bucket PnL");
    assert.match(bucketPnlSortTitle("pnl_optimal_usd"), /USD/);
    assert.match(bucketPnlSortTitle("pnl_optimal_bps"), /bps/);
  });

  it("header click cycles usd↓ → usd↑ → bps↓ → bps↑ → usd↓", () => {
    let s = nextPnlSortState("pnl_optimal_usd", true);
    assert.deepEqual(s, { key: "pnl_optimal_usd", desc: false });
    s = nextPnlSortState(s.key, s.desc);
    assert.deepEqual(s, { key: "pnl_optimal_bps", desc: true });
    s = nextPnlSortState(s.key, s.desc);
    assert.deepEqual(s, { key: "pnl_optimal_bps", desc: false });
    s = nextPnlSortState(s.key, s.desc);
    assert.deepEqual(s, { key: "pnl_optimal_usd", desc: true });
  });
});

describe("dexNonTradeableReason (WHI-796)", () => {
  it("returns null for tradeable rows", () => {
    assert.equal(
      dexNonTradeableReason(
        row({ pair_id: "OK", amm_mid: "1", low_liquidity: false }),
      ),
      null,
    );
    assert.equal(
      dexNonTradeableReason(
        row({
          pair_id: "RFQ",
          amm_mid: null,
          low_liquidity: true,
          rfq_buy: "1",
          rfq_sell: "2",
        }),
      ),
      null,
    );
  });

  it("returns structured reasons for empty / invalid / no pool / low liq / no quote", () => {
    assert.equal(
      dexNonTradeableReason(
        row({
          pair_id: "E",
          amm_mid: null,
          amm_quote_reason: "empty_pool",
          low_liquidity: true,
        }),
      ),
      "empty_pool",
    );
    assert.equal(
      dexNonTradeableReason(
        row({
          pair_id: "I",
          amm_mid: null,
          amm_quote_reason: "invalid_mid",
          low_liquidity: true,
        }),
      ),
      "invalid_mid",
    );
    assert.equal(
      dexNonTradeableReason(
        row({
          pair_id: "N",
          amm_mid: null,
          low_liquidity: true,
          pnl_v2: {
            status: "no_pool",
            has_depth: false,
            direction: null,
            optimal_notional_usd: null,
            optimal_net_pnl_usd: null,
            optimal_net_pnl_bps: null,
            bybit_depth_source: null,
          },
        }),
      ),
      "no_pool",
    );
    assert.equal(
      dexNonTradeableReason(
        row({ pair_id: "D", amm_mid: "1", low_liquidity: true }),
      ),
      "low_liq",
    );
    // Cold-start: expected pool, not low-liq, mid missing → no_quote (always badge).
    assert.equal(
      dexNonTradeableReason(
        row({ pair_id: "WAIT", amm_mid: null, low_liquidity: false }),
      ),
      "no_quote",
    );
  });

});

describe("overview URL state (WHI-791)", () => {
  it("parses sort / desc / all from search string", () => {
    assert.deepEqual(parseOverviewSearch("?sort=tvl_usd&desc=1&all=1"), {
      sortKey: "tvl_usd",
      sortDesc: true,
      showAll: true,
    });
    // Missing `all` → leave showAll unset (caller keeps default collapsed).
    assert.deepEqual(parseOverviewSearch("sort=cex_volume_24h&desc=0"), {
      sortKey: "cex_volume_24h",
      sortDesc: false,
    });
    assert.deepEqual(parseOverviewSearch(""), {});
    assert.deepEqual(parseOverviewSearch("?sort=not_a_key"), {});
    // Lone desc must not pin client default over the market API sort_key.
    assert.deepEqual(parseOverviewSearch("?desc=0"), {});
    assert.deepEqual(parseOverviewSearch("?sort=tvl_usd&all=0"), {
      sortKey: "tvl_usd",
      showAll: false,
    });
  });

  it("builds shareable search from current view state", () => {
    assert.equal(
      buildOverviewSearch({
        sortKey: "tvl_usd",
        sortDesc: true,
        showAll: false,
      }),
      "?sort=tvl_usd&desc=1",
    );
    assert.equal(
      buildOverviewSearch({
        sortKey: "net_edge",
        sortDesc: false,
        showAll: true,
      }),
      "?sort=net_edge&desc=0&all=1",
    );
  });
});
