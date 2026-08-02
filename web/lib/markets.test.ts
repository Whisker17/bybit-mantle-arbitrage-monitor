import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  DEFAULT_MARKET_ID,
  KNOWN_MARKETS,
  isKnownMarketId,
  marketAccumulatingMessage,
  marketApiHealthPath,
  marketApiPairPath,
  marketApiPairsPath,
  marketOverviewPath,
  marketPairPath,
} from "./markets";

describe("markets helpers", () => {
  it("knows default and both inventory markets", () => {
    assert.equal(DEFAULT_MARKET_ID, "bybit-fluxion");
    assert.equal(KNOWN_MARKETS.length, 2);
    assert.ok(isKnownMarketId("bybit-fluxion"));
    assert.ok(isKnownMarketId("binance-pancake"));
    assert.equal(isKnownMarketId("nope"), false);
  });

  it("builds overview and pair URL paths with trailing slash for static export", () => {
    assert.equal(marketOverviewPath("bybit-fluxion"), "/m/bybit-fluxion/");
    assert.equal(
      marketPairPath("bybit-fluxion", "AAPLx"),
      "/m/bybit-fluxion/pair/AAPLx/",
    );
    assert.equal(
      marketPairPath("binance-pancake", "TSLAB"),
      "/m/binance-pancake/pair/TSLAB/",
    );
  });

  it("builds market-scoped API paths", () => {
    assert.equal(
      marketApiPairsPath("bybit-fluxion"),
      "/api/bybit-fluxion/pairs",
    );
    assert.equal(
      marketApiHealthPath("binance-pancake"),
      "/api/binance-pancake/health",
    );
    assert.equal(
      marketApiPairPath("bybit-fluxion", "AAPLx"),
      "/api/bybit-fluxion/pairs/AAPLx",
    );
  });

  it("accumulating copy is explicit (no dashed placeholder)", () => {
    assert.match(marketAccumulatingMessage("Binance ⇄ PancakeSwap"), /accumulat/i);
    assert.match(marketAccumulatingMessage(null), /market/i);
  });

  it("binance market hides RFQ in known card", () => {
    const b = KNOWN_MARKETS.find((m) => m.id === "binance-pancake");
    assert.ok(b);
    assert.equal(b.has_rfq, false);
    const y = KNOWN_MARKETS.find((m) => m.id === "bybit-fluxion");
    assert.ok(y);
    assert.equal(y.has_rfq, true);
  });
});
