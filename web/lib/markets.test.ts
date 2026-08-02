import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
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
  parseMarketYaml,
} from "./markets";

describe("markets helpers", () => {
  it("loads both inventory markets from config/markets YAML", () => {
    assert.equal(DEFAULT_MARKET_ID, "bybit-fluxion");
    assert.equal(KNOWN_MARKETS.length, 2);
    assert.ok(isKnownMarketId("bybit-fluxion"));
    assert.ok(isKnownMarketId("binance-pancake"));
    assert.equal(isKnownMarketId("nope"), false);
  });

  it("display_name / has_rfq match the checked-in market YAML files", () => {
    const dir = [
      path.join(process.cwd(), "config", "markets"),
      path.join(process.cwd(), "..", "config", "markets"),
    ].find((d) => fs.existsSync(d));
    assert.ok(dir);
    for (const card of KNOWN_MARKETS) {
      const text = fs.readFileSync(path.join(dir!, `${card.id}.yaml`), "utf8");
      const parsed = parseMarketYaml(text);
      assert.equal(card.display_name, parsed.display_name);
      assert.equal(card.has_rfq, parsed.has_rfq);
      assert.equal(card.cex_venue, parsed.cex_venue);
    }
    const bybit = KNOWN_MARKETS.find((m) => m.id === "bybit-fluxion")!;
    const bsc = KNOWN_MARKETS.find((m) => m.id === "binance-pancake")!;
    assert.equal(bybit.has_rfq, true);
    assert.equal(bsc.has_rfq, false);
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
});
