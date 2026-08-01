import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { filterRows, sortRows } from "./sort";
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
