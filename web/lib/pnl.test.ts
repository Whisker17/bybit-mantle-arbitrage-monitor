/**
 * PnL v2 display helpers — empty / negative / thin-depth states (WHI-766).
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  isThinDepth,
  overviewPnlCell,
  pickBucketTable,
} from "./pnl";
import type {
  PnlBucketTable,
  PnlOptimalSummary,
  PnlPairSnapshot,
  PnlResult,
} from "./types";

function bucket(
  size: string,
  pnl: string,
  fillable = true,
  reason: string | null = null,
): PnlResult {
  return {
    pair_id: "AAPLx",
    venue: "amm",
    direction: "buy_fluxion_sell_bybit",
    size_usd: size,
    q_base: "1",
    bybit_mid: "100",
    spent_usd: size,
    recv_usd: size,
    pnl_usd: pnl,
    pnl_bps: null,
    fillable,
    reason,
    bybit_depth_source: "book",
    meets_min_profit: false,
    costs: {
      bybit_fee_usd: "0.1",
      bybit_slip_usd: "0",
      fluxion_fee_usd: "0",
      fluxion_slip_usd: "0",
      gas_usd: "0.01",
      basis_usd: "0",
    },
  };
}

describe("overviewPnlCell", () => {
  it("shows no depth when journal has no depth curve", () => {
    const pnl: PnlOptimalSummary = {
      status: "no_depth",
      has_depth: false,
      direction: null,
      optimal_notional_usd: null,
      optimal_net_pnl_usd: null,
      optimal_net_pnl_bps: null,
      bybit_depth_source: null,
    };
    const cell = overviewPnlCell(pnl);
    assert.equal(cell.kind, "status");
    if (cell.kind === "status") {
      assert.equal(cell.label, "no depth");
    }
  });

  it("renders negative optimal PnL as ok cell", () => {
    const pnl: PnlOptimalSummary = {
      status: "ok",
      has_depth: true,
      direction: "buy_fluxion_sell_bybit",
      optimal_notional_usd: "1000",
      optimal_net_pnl_usd: "-1.25",
      optimal_net_pnl_bps: "-12.5",
      bybit_depth_source: "book",
    };
    const cell = overviewPnlCell(pnl);
    assert.equal(cell.kind, "ok");
    if (cell.kind === "ok") {
      assert.equal(cell.pnlUsd, "-1.25");
      assert.match(cell.title, /F→B/);
    }
  });

  it("empty when payload missing", () => {
    const cell = overviewPnlCell(undefined);
    assert.equal(cell.kind, "empty");
  });
});

describe("isThinDepth / pickBucketTable", () => {
  it("detects partial unfillable large buckets", () => {
    const table: PnlBucketTable = {
      pair_id: "AAPLx",
      direction: "buy_fluxion_sell_bybit",
      amm_buckets: [
        bucket("10", "-0.02"),
        bucket("50", "-0.06"),
        bucket("10000", "0", false, "bybit_book_unfillable"),
      ],
      rfq_rows: [],
      optimal: null,
    };
    assert.equal(isThinDepth(table), true);

    const allOk: PnlBucketTable = {
      ...table,
      amm_buckets: [bucket("10", "-0.02"), bucket("50", "-0.06")],
    };
    assert.equal(isThinDepth(allOk), false);
  });

  it("picks direction table from snapshot", () => {
    const snap: PnlPairSnapshot = {
      status: "ok",
      has_depth: true,
      best: {
        status: "ok",
        has_depth: true,
        direction: "buy_fluxion_sell_bybit",
        optimal_notional_usd: "100",
        optimal_net_pnl_usd: "1",
        optimal_net_pnl_bps: "10",
        bybit_depth_source: "book",
      },
      tables: {
        buy_fluxion_sell_bybit: {
          pair_id: "AAPLx",
          direction: "buy_fluxion_sell_bybit",
          amm_buckets: [bucket("10", "0.1")],
          rfq_rows: [],
          optimal: null,
        },
      },
    };
    assert.ok(pickBucketTable(snap, "buy_fluxion_sell_bybit"));
    assert.equal(pickBucketTable(snap, "buy_bybit_sell_fluxion"), null);
  });
});
