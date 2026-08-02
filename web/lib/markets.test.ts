import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";

import {
  DEFAULT_MARKET_ID,
  KNOWN_MARKET_IDS,
  isKnownMarketId,
  marketAccumulatingMessage,
  marketApiHealthPath,
  marketApiPairPath,
  marketApiPairsPath,
  marketOverviewPath,
  marketPairPath,
} from "./markets";
import {
  assertKnownMarketIdsMatchDisk,
  loadKnownMarketsFromDisk,
  parseMarketYaml,
} from "./markets-server";

describe("markets helpers", () => {
  it("KNOWN_MARKET_IDS matches config/markets YAML on disk", () => {
    assert.equal(DEFAULT_MARKET_ID, "bybit-fluxion");
    assert.ok(isKnownMarketId("bybit-fluxion"));
    assert.ok(isKnownMarketId("binance-pancake"));
    assert.equal(isKnownMarketId("nope"), false);
    assertKnownMarketIdsMatchDisk();
  });

  it("loadKnownMarketsFromDisk has_rfq matches YAML", () => {
    const cards = loadKnownMarketsFromDisk();
    const dir = [
      path.join(process.cwd(), "config", "markets"),
      path.join(process.cwd(), "..", "config", "markets"),
    ].find((d) => fs.existsSync(d));
    assert.ok(dir);
    for (const card of cards) {
      const text = fs.readFileSync(path.join(dir!, `${card.id}.yaml`), "utf8");
      const parsed = parseMarketYaml(text);
      assert.equal(card.display_name, parsed.display_name);
      assert.equal(card.has_rfq, parsed.has_rfq);
    }
    const bybit = cards.find((m) => m.id === "bybit-fluxion")!;
    const bsc = cards.find((m) => m.id === "binance-pancake")!;
    assert.equal(bybit.has_rfq, true);
    assert.equal(bsc.has_rfq, false);
    assert.deepEqual(
      new Set(KNOWN_MARKET_IDS),
      new Set(cards.map((c) => c.id)),
    );
  });

  it("builds overview and pair URL paths with trailing slash for static export", () => {
    assert.equal(marketOverviewPath("bybit-fluxion"), "/m/bybit-fluxion/");
    assert.equal(
      marketPairPath("bybit-fluxion", "AAPLx"),
      "/m/bybit-fluxion/pair/AAPLx/",
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
