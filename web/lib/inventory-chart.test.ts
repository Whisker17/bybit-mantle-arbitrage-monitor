import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { prepareInventorySeries } from "./inventory-chart";
import type { MmAddressSeries } from "./types";

describe("prepareInventorySeries", () => {
  it("maps cumulative inventory points for chart", () => {
    const input: MmAddressSeries[] = [
      {
        address: "0x" + "aa".repeat(20),
        label: "market_maker",
        evidence_summary: "rfq",
        is_rebalancer: false,
        final_inventory: "1.5",
        series: [
          {
            ts_ms: 1_700_000_000_000,
            inventory: "1",
            tx_hash: "0x1",
            kind: "rfq_fill",
            delta_native: "1",
          },
          {
            ts_ms: 1_700_000_010_000,
            inventory: "1.5",
            tx_hash: "0x2",
            kind: "rfq_fill",
            delta_native: "0.5",
          },
        ],
      },
    ];
    const out = prepareInventorySeries(input);
    assert.equal(out.length, 1);
    assert.equal(out[0].ys.length, 2);
    assert.equal(out[0].ys[1], 1.5);
    assert.equal(out[0].finalInventory, 1.5);
    assert.equal(out[0].xs[0], 1_700_000_000);
  });

  it("skips empty series", () => {
    const out = prepareInventorySeries([
      {
        address: "0xbb",
        label: "market_maker",
        evidence_summary: null,
        is_rebalancer: false,
        final_inventory: "0",
        series: [],
      },
    ]);
    assert.equal(out.length, 0);
  });
});
