/**
 * PnL v2 display helpers — empty / negative / thin-depth states (WHI-766).
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  failsDriftBar,
  formatDriftRequirement,
  isCapturableOpportunity,
  isOptimalBucket,
  isThinDepth,
  overviewNetCell,
  overviewPnlCell,
  pickBucketTable,
  quoteAgeTitle,
  quoteAgedHint,
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
      withdrawal_fee_usd: "0",
      withdrawal_fee_kind: "stable",
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

  it("does not collapse no_book/stale into no depth", () => {
    for (const status of [
      "no_book",
      "no_pool",
      "empty_pool",
      "invalid_mid",
      "pricing_anomaly",
      "stale",
    ] as const) {
      const cell = overviewPnlCell({
        status,
        has_depth: false,
        direction: null,
        optimal_notional_usd: null,
        optimal_net_pnl_usd: null,
        optimal_net_pnl_bps: null,
        bybit_depth_source: null,
      });
      assert.equal(cell.kind, "status");
      if (cell.kind === "status") {
        assert.notEqual(cell.label, "no depth");
        assert.match(cell.label, /book|pool|aged|mid|anomaly/);
      }
    }
  });

  it("keeps numbers when quote_aged and annotates title (WHI-821)", () => {
    const cell = overviewPnlCell({
      status: "ok",
      has_depth: true,
      direction: "buy_fluxion_sell_bybit",
      optimal_notional_usd: "1000",
      optimal_net_pnl_usd: "2.5",
      optimal_net_pnl_bps: "25",
      bybit_depth_source: "book",
      quote_aged: true,
      cex_quote_age_ms: 45_000,
      amm_quote_age_ms: 800,
    });
    assert.equal(cell.kind, "ok");
    if (cell.kind === "ok") {
      assert.equal(cell.pnlUsd, "2.5");
      assert.equal(cell.quoteAged, true);
      assert.equal(cell.ageHint, "aged 45s");
      assert.match(cell.title, /quote aged/);
      assert.match(cell.title, /CEX/);
    }
  });

  it("shares quoteAgeTitle / quoteAgedHint for overview and detail", () => {
    const ages = {
      quote_aged: true,
      cex_quote_age_ms: 45_000,
      amm_quote_age_ms: 800,
      depth_quote_age_ms: 800,
    };
    assert.equal(quoteAgedHint(ages), "aged 45s");
    assert.match(quoteAgeTitle(ages), /CEX 45s ago/);
    assert.match(quoteAgeTitle(ages), /AMM/);
  });

  it("overviewNetCell surfaces Q* size and quote_aged parity (WHI-966)", () => {
    const aged = overviewNetCell({
      net_edge_bps: "12.5",
      net_size_usd: "250",
      pnl_v2: {
        quote_aged: true,
        cex_quote_age_ms: 45_000,
        amm_quote_age_ms: 800,
      },
    });
    assert.equal(aged.sizeLabel, "Q* $250");
    assert.equal(aged.quoteAged, true);
    assert.equal(aged.ageHint, "aged 45s");
    assert.match(aged.title, /Q\*=\$250/);
    assert.match(aged.title, /quote aged/);

    const blank = overviewNetCell({
      net_edge_bps: null,
      net_size_usd: null,
      pnl_v2: { status: "no_depth", quote_aged: false },
    });
    assert.equal(blank.sizeLabel, null);
    assert.equal(blank.emptyLabel, "no depth");
    assert.equal(blank.quoteAged, false);
    assert.match(blank.title, /no depth/);
  });
});

describe("overviewNetCell empty title passthrough", () => {
  it("uses caller empty title when Net is blank", () => {
    const cell = overviewNetCell(
      { net_edge_bps: null, net_size_usd: null },
      "empty pool",
    );
    assert.equal(cell.title, "empty pool");
  });

  it("maps ok+null-bps blank Net to unfillable (parity with Bucket PnL)", () => {
    const cell = overviewNetCell({
      net_edge_bps: null,
      net_size_usd: null,
      pnl_v2: { status: "ok", quote_aged: false },
    });
    assert.equal(cell.emptyLabel, "unfillable");
  });
});

describe("WHI-962 sequential drift bar", () => {
  it("CRCLx-style net ≪ 1.5×σ is not capturable", () => {
    assert.equal(
      isCapturableOpportunity({
        pnl_v2: {
          meets_min_profit: true,
          optimal_net_pnl_usd: "1.23",
        },
        clears_drift_optimal: false,
      }),
      false,
    );
    assert.equal(failsDriftBar({ clears_drift_optimal: false }), true);
  });

  it("+$0.40 fails min_profit floor even when drift clears", () => {
    assert.equal(
      isCapturableOpportunity({
        pnl_v2: {
          meets_min_profit: false,
          optimal_net_pnl_usd: "0.40",
        },
        clears_drift_optimal: true,
      }),
      false,
    );
  });

  it("green only when floors + drift both clear", () => {
    assert.equal(
      isCapturableOpportunity({
        pnl_v2: {
          meets_min_profit: true,
          optimal_net_pnl_usd: "2.5",
        },
        clears_drift_optimal: true,
      }),
      true,
    );
  });

  it("missing σ does not fail-closed (binance-pancake)", () => {
    assert.equal(
      isCapturableOpportunity({
        pnl_v2: {
          meets_min_profit: true,
          optimal_net_pnl_usd: "3",
        },
        clears_drift_optimal: null,
      }),
      true,
    );
  });

  it("overviewNetCell sets capturable/failsDrift for AC2 muting", () => {
    const fails = overviewNetCell({
      net_edge_bps: "15",
      net_size_usd: "500",
      pnl_v2: {
        meets_min_profit: true,
        optimal_net_pnl_usd: "1.23",
      },
      clears_drift_optimal: false,
      sigma_transit_bps: "68.83",
      drift_premium_bps: "103.245",
      drift_premium_k: "1.5",
    });
    assert.equal(fails.capturable, false);
    assert.equal(fails.failsDrift, true);
    assert.match(fails.title, /Fails sequential bar/);

    const ok = overviewNetCell({
      net_edge_bps: "120",
      net_size_usd: "500",
      pnl_v2: {
        meets_min_profit: true,
        optimal_net_pnl_usd: "6",
      },
      clears_drift_optimal: true,
      sigma_transit_bps: "24.85",
      drift_premium_bps: "37.275",
      drift_premium_k: "1.5",
    });
    assert.equal(ok.capturable, true);
    assert.equal(ok.failsDrift, false);
  });

  it("overviewPnlCell mutes when drift fails", () => {
    const pnl: PnlOptimalSummary = {
      status: "ok",
      has_depth: true,
      direction: "buy_fluxion_sell_bybit",
      optimal_notional_usd: "500",
      optimal_net_pnl_usd: "1.23",
      optimal_net_pnl_bps: "15",
      bybit_depth_source: "book",
      meets_min_profit: true,
    };
    const cell = overviewPnlCell(pnl, null, null, {
      clears_drift_optimal: false,
      sigma_transit_bps: "68.83",
      drift_premium_bps: "103.245",
      drift_premium_k: "1.5",
      net_edge_bps: "15",
    });
    assert.equal(cell.kind, "ok");
    if (cell.kind === "ok") {
      assert.equal(cell.capturable, false);
      assert.equal(cell.failsDrift, true);
      assert.match(cell.title, /Fails sequential bar/);
    }
  });

  it("formatDriftRequirement renders requirement line", () => {
    const line = formatDriftRequirement(
      {
        sigma_transit_bps: "68.83",
        drift_premium_k: "1.5",
        drift_premium_bps: "103.245",
        session: "open",
        clears_drift: {
          buy_fluxion_sell_bybit: false,
          buy_bybit_sell_fluxion: null,
        },
        clears_drift_optimal: false,
      },
      "15",
    );
    assert.ok(line);
    assert.match(line!, /fails/);
    assert.match(line!, /103\.245/);
  });
});

describe("status labels", () => {
  it("labels empty_pool / invalid_mid / pricing_anomaly distinctly from no_pool", () => {
    const empty = overviewPnlCell({
      status: "empty_pool",
      has_depth: false,
      direction: null,
      optimal_notional_usd: null,
      optimal_net_pnl_usd: null,
      optimal_net_pnl_bps: null,
      bybit_depth_source: null,
    });
    const invalid = overviewPnlCell({
      status: "invalid_mid",
      has_depth: false,
      direction: null,
      optimal_notional_usd: null,
      optimal_net_pnl_usd: null,
      optimal_net_pnl_bps: null,
      bybit_depth_source: null,
    });
    const anomaly = overviewPnlCell({
      status: "pricing_anomaly",
      has_depth: true,
      direction: null,
      optimal_notional_usd: null,
      optimal_net_pnl_usd: null,
      optimal_net_pnl_bps: null,
      bybit_depth_source: "book",
    });
    assert.equal(anomaly.kind, "status");
    if (anomaly.kind === "status") {
      assert.equal(anomaly.label, "price anomaly");
    }
    const none = overviewPnlCell({
      status: "no_pool",
      has_depth: false,
      direction: null,
      optimal_notional_usd: null,
      optimal_net_pnl_usd: null,
      optimal_net_pnl_bps: null,
      bybit_depth_source: null,
    });
    assert.equal(empty.kind, "status");
    assert.equal(invalid.kind, "status");
    assert.equal(none.kind, "status");
    if (
      empty.kind === "status" &&
      invalid.kind === "status" &&
      none.kind === "status"
    ) {
      assert.equal(empty.label, "empty pool");
      assert.equal(invalid.label, "invalid mid");
      assert.equal(none.label, "no pool");
    }
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

  it("highlights nearest fillable bucket to Q*", () => {
    const table: PnlBucketTable = {
      pair_id: "AAPLx",
      direction: "buy_fluxion_sell_bybit",
      amm_buckets: [
        bucket("10", "-0.1"),
        bucket("100", "1.0"),
        bucket("1000", "0.5"),
      ],
      rfq_rows: [],
      optimal: {
        pair_id: "AAPLx",
        direction: "buy_fluxion_sell_bybit",
        q_star_usd: "87.3",
        pnl_usd: "1.1",
        q_min_usd: "10",
        q_max_usd: "10000",
        depth_cap_usd: null,
        amm_cap_usd: "10000",
        samples_evaluated: 40,
        result: bucket("87.3", "1.1"),
      },
    };
    assert.equal(isOptimalBucket(bucket("100", "1.0"), table), true);
    assert.equal(isOptimalBucket(bucket("10", "-0.1"), table), false);
  });
});
