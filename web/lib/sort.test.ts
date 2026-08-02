import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  applyTopN,
  buildOverviewSearch,
  filterRows,
  parseOverviewSearch,
  sortKeyLabel,
  sortRows,
  topNSummary,
  TOP_N_DEFAULT,
} from "./sort";
import type { PairOverviewRow } from "./types";

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
});

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

describe("applyTopN (WHI-791)", () => {
  it("defaults TOP_N_DEFAULT to 10", () => {
    assert.equal(TOP_N_DEFAULT, 10);
  });

  it("collapsed: keeps only top N present values; nulls do not fill slots", () => {
    // Already sorted by tvl desc with nulls last (sortRows contract).
    const sorted = [
      row({ pair_id: "A", tvl_usd: "500" }),
      row({ pair_id: "B", tvl_usd: "400" }),
      row({ pair_id: "C", tvl_usd: "300" }),
      row({ pair_id: "D", tvl_usd: null }),
      row({ pair_id: "E" }), // missing tvl
    ];
    const view = applyTopN(sorted, "tvl_usd", { n: 2, showAll: false });
    assert.deepEqual(
      view.rows.map((r) => r.pair_id),
      ["A", "B"],
    );
    assert.equal(view.presentCount, 3);
    assert.equal(view.totalCount, 5);
    assert.equal(view.shownCount, 2);
    assert.equal(view.isTruncated, true);
    assert.equal(view.showAll, false);
  });

  it("collapsed: when fewer than N have values, shows only present (no n/a padding)", () => {
    const sorted = [
      row({ pair_id: "A", cex_volume_24h: "100" }),
      row({ pair_id: "B", cex_volume_24h: null }),
      row({ pair_id: "C", cex_volume_24h: null }),
    ];
    const view = applyTopN(sorted, "cex_volume_24h", { n: 10, showAll: false });
    assert.deepEqual(
      view.rows.map((r) => r.pair_id),
      ["A"],
    );
    assert.equal(view.shownCount, 1);
    // Full list still larger than shown → truncated for "show all" affordance.
    assert.equal(view.isTruncated, true);
  });

  it("showAll: returns full sorted list including trailing n/a", () => {
    const sorted = [
      row({ pair_id: "A", tvl_usd: "10" }),
      row({ pair_id: "B", tvl_usd: null }),
    ];
    const view = applyTopN(sorted, "tvl_usd", { n: 10, showAll: true });
    assert.deepEqual(
      view.rows.map((r) => r.pair_id),
      ["A", "B"],
    );
    assert.equal(view.isTruncated, false);
    assert.equal(view.showAll, true);
  });

  it("works after sortRows on unsorted input (TVL n/a last, not in top N)", () => {
    const rows = [
      row({ pair_id: "NA", tvl_usd: null }),
      row({ pair_id: "LO", tvl_usd: "1" }),
      row({ pair_id: "HI", tvl_usd: "99" }),
      row({ pair_id: "MID", tvl_usd: "50" }),
    ];
    const sorted = sortRows(rows, "tvl_usd", true);
    const view = applyTopN(sorted, "tvl_usd", { n: 2, showAll: false });
    assert.deepEqual(
      view.rows.map((r) => r.pair_id),
      ["HI", "MID"],
    );
  });
});

describe("topNSummary / sortKeyLabel (WHI-791)", () => {
  it("labels known sort keys", () => {
    assert.equal(sortKeyLabel("tvl_usd"), "TVL");
    assert.equal(sortKeyLabel("cex_volume_24h"), "CEX Vol");
  });

  it("reports Top N of total by key when collapsed", () => {
    const sorted = Array.from({ length: 55 }, (_, i) =>
      row({ pair_id: `P${i}`, tvl_usd: String(1000 - i) }),
    );
    const view = applyTopN(sorted, "tvl_usd", { n: 10, showAll: false });
    assert.equal(topNSummary(view, "tvl_usd"), "Top 10 of 55 by TVL");
  });

  it("notes how many rows have data when some are n/a", () => {
    const sorted = [
      row({ pair_id: "A", tvl_usd: "10" }),
      row({ pair_id: "B", tvl_usd: "9" }),
      row({ pair_id: "C", tvl_usd: null }),
    ];
    const view = applyTopN(sorted, "tvl_usd", { n: 10, showAll: false });
    assert.equal(
      topNSummary(view, "tvl_usd"),
      "Top 2 of 3 by TVL · 2 with data",
    );
  });

  it("reports full list when expanded", () => {
    const sorted = [
      row({ pair_id: "A", tvl_usd: "1" }),
      row({ pair_id: "B", tvl_usd: "2" }),
    ];
    const view = applyTopN(sorted, "tvl_usd", { n: 10, showAll: true });
    assert.equal(
      topNSummary(view, "tvl_usd"),
      "Showing all 2 pairs · sorted by TVL",
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
