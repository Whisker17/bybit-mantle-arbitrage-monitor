import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { prepareSpreadSeries, sessionOpenBands } from "./spread-chart";
import type { SpreadPoint } from "./types";

describe("sessionOpenBands", () => {
  it("groups contiguous open sessions", () => {
    assert.deepEqual(
      sessionOpenBands(["closed", "open", "open", "closed", "open"]),
      [
        [1, 2],
        [4, 4],
      ],
    );
    assert.deepEqual(sessionOpenBands([]), []);
    assert.deepEqual(sessionOpenBands(["closed", "closed"]), []);
  });
});

describe("prepareSpreadSeries", () => {
  it("returns empty series for no data", () => {
    const s = prepareSpreadSeries([]);
    assert.equal(s.xs.length, 0);
    assert.equal(s.hasAmm, false);
  });

  it("parses decimals and flags presence", () => {
    const points: SpreadPoint[] = [
      {
        ts_ms: 1_700_000_000_000,
        amm_spread_bps: "-12.5",
        rfq_spread_bps: "3.0",
        bybit_mid: "100.1",
        session: "open",
      },
      {
        ts_ms: 1_700_000_001_000,
        amm_spread_bps: null,
        rfq_spread_bps: null,
        bybit_mid: "100.2",
        session: "closed",
      },
    ];
    const s = prepareSpreadSeries(points);
    assert.equal(s.xs.length, 2);
    assert.equal(s.amm[0], -12.5);
    assert.equal(s.amm[1], null);
    assert.equal(s.rfq[0], 3.0);
    assert.equal(s.bybitMid[1], 100.2);
    assert.equal(s.hasAmm, true);
    assert.equal(s.hasRfq, true);
    assert.equal(s.hasMid, true);
    assert.deepEqual(s.openBands, [[0, 0]]);
  });
});
