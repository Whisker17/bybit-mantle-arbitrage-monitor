import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  alignInventorySeries,
  prepareInventorySeries,
} from "./inventory-chart";
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

describe("alignInventorySeries", () => {
  it("forward-fills multi-address alignment so lines stay continuous", () => {
    const prepared = prepareInventorySeries([
      {
        address: "0x" + "aa".repeat(20),
        label: "market_maker",
        evidence_summary: null,
        is_rebalancer: false,
        final_inventory: "2",
        series: [
          {
            ts_ms: 1_000_000,
            inventory: "1",
            tx_hash: "0x1",
            kind: "rfq_fill",
            delta_native: "1",
          },
          {
            ts_ms: 3_000_000,
            inventory: "2",
            tx_hash: "0x2",
            kind: "rfq_fill",
            delta_native: "1",
          },
        ],
      },
      {
        address: "0x" + "bb".repeat(20),
        label: "market_maker",
        evidence_summary: null,
        is_rebalancer: false,
        final_inventory: "5",
        series: [
          {
            ts_ms: 2_000_000,
            inventory: "5",
            tx_hash: "0x3",
            kind: "amm_swap",
            delta_native: "5",
          },
        ],
      },
    ]);
    const { xs, columns } = alignInventorySeries(prepared);
    assert.deepEqual(xs, [1000, 2000, 3000]);
    // Address A: 1 at t0, forward-filled at t1, 2 at t2
    assert.deepEqual(columns[0], [1, 1, 2]);
    // Address B: null before first event, 5 thereafter
    assert.deepEqual(columns[1], [null, 5, 5]);
  });
});
