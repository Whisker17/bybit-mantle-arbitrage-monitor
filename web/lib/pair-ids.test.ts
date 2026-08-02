import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  loadAllMarketPairParams,
  loadMarketIds,
  loadPairIdsFromConfig,
} from "./pair-ids";

describe("loadPairIdsFromConfig", () => {
  it("parses the 11 monitor pair ids from config/markets/bybit-fluxion.yaml", () => {
    // process.cwd() is web/ when npm test runs from web/
    const ids = loadPairIdsFromConfig();
    assert.equal(ids.length, 11);
    assert.deepEqual(ids, [
      "AAPLx",
      "CRCLx",
      "GOOGLx",
      "HOODx",
      "METAx",
      "NVDAx",
      "TSLAx",
      "SPCXx",
      "AMZNx",
      "COINx",
      "MCDx",
    ]);
  });

  it("parses binance-pancake inventory pair ids", () => {
    const ids = loadPairIdsFromConfig("binance-pancake");
    assert.ok(ids.length >= 1);
    assert.ok(ids.includes("TSLAB") || ids.includes("SPCXB"));
  });

  it("loadAllMarketPairParams covers both markets", () => {
    const params = loadAllMarketPairParams();
    const markets = new Set(params.map((p) => p.market));
    assert.ok(markets.has("bybit-fluxion"));
    assert.ok(markets.has("binance-pancake"));
    assert.ok(params.some((p) => p.pairId === "AAPLx"));
  });

  it("loadMarketIds matches known markets", () => {
    assert.deepEqual(new Set(loadMarketIds()), new Set(["bybit-fluxion", "binance-pancake"]));
  });
});
