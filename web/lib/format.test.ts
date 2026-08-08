/**
 * Lightweight node:test suite for pure format/sort helpers.
 * Run: `npx tsx --test lib/format.test.ts lib/sort.test.ts`
 * (optional; `npm run build` is the gate for static export).
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  ammQuoteReasonLabel,
  bpsTone,
  cexPremiumBps,
  dexNonTradeableLabel,
  dexNonTradeableTitle,
  directionToggleLabel,
  explorerTxUrl,
  fmtDirection,
  fmtDirectionTitle,
  fmtNotional,
  fmtReferenceSize,
  fmtOrAmmReason,
  fmtPct,
  fmtPrice,
  fmtSession,
  fmtSignedBps,
  fmtUsd,
  fmtUtcHm,
  fmtVolumeRatio,
  fmtUnderlyingPriceType,
  isRealUnderlyingPrint,
  shortAddr,
  totalWearBps,
  usdTone,
  venueCode,
  venueLabel,
  venuesFromMarketId,
} from "./format";

describe("fmtUnderlyingPriceType (WHI-821)", () => {
  it("maps stale; keeps raw type over premium-relative labels", () => {
    assert.equal(fmtUnderlyingPriceType("stale", "price stale"), "price stale");
    assert.equal(fmtUnderlyingPriceType("stale", null), "price stale");
    // WHI-783: Underlying is reference-only — do not show "vs close".
    assert.equal(fmtUnderlyingPriceType("close", "vs close"), "close");
    assert.equal(fmtUnderlyingPriceType("pre", "vs pre"), "pre");
    assert.equal(fmtUnderlyingPriceType("live", null), "live");
    assert.equal(fmtUnderlyingPriceType(null, "vs close"), "vs close");
    assert.equal(fmtUnderlyingPriceType(null, null), null);
  });
});

describe("ammQuoteReasonLabel", () => {
  it("maps wire reasons to human labels", () => {
    assert.equal(ammQuoteReasonLabel("empty_pool"), "empty pool");
    assert.equal(ammQuoteReasonLabel("invalid_mid"), "invalid mid");
    assert.equal(ammQuoteReasonLabel("pricing_anomaly"), "price anomaly");
    assert.equal(ammQuoteReasonLabel(null), null);
    assert.equal(ammQuoteReasonLabel(undefined), null);
  });
});

describe("dexNonTradeableLabel / title (WHI-796 / WHI-822)", () => {
  it("maps structured reasons to stable UI labels", () => {
    assert.equal(dexNonTradeableLabel("empty_pool"), "empty pool");
    assert.equal(dexNonTradeableLabel("invalid_mid"), "invalid mid");
    assert.equal(dexNonTradeableLabel("pricing_anomaly"), "price anomaly");
    assert.equal(dexNonTradeableLabel("no_pool"), "no pool");
    assert.equal(dexNonTradeableLabel("low_liq"), "low liq");
    assert.equal(dexNonTradeableLabel("no_quote"), "no quote");
    assert.equal(dexNonTradeableLabel(null), null);
  });

  it("provides hover titles for every reason", () => {
    assert.ok(dexNonTradeableTitle("empty_pool")?.includes("liquidity"));
    assert.ok(dexNonTradeableTitle("pricing_anomaly")?.includes("tradable"));
    assert.ok(dexNonTradeableTitle("no_pool")?.includes("Top-N"));
    assert.ok(dexNonTradeableTitle("no_quote")?.includes("Waiting"));
    assert.equal(dexNonTradeableTitle(null), undefined);
  });
});

describe("fmtOrAmmReason", () => {
  it("keeps formatted value when present", () => {
    assert.equal(fmtOrAmmReason("+10.0", "10", "empty_pool"), "+10.0");
  });
  it("substitutes reason when value missing", () => {
    assert.equal(fmtOrAmmReason("—", null, "empty_pool"), "empty pool");
    assert.equal(fmtOrAmmReason("—", null, null), "—");
  });
});

describe("cexPremiumBps", () => {
  it("prefers cex_premium_bps over legacy premium_bps", () => {
    assert.equal(
      cexPremiumBps({ cex_premium_bps: "5", premium_bps: "99" }),
      "5",
    );
    assert.equal(cexPremiumBps({ premium_bps: "10" }), "10");
    assert.equal(cexPremiumBps({}), null);
    assert.equal(cexPremiumBps(null), null);
  });
});

describe("fmtPrice", () => {
  it("formats finite numbers and blanks nulls", () => {
    assert.equal(fmtPrice("100.12345"), "100.1235");
    assert.equal(fmtPrice(null), "—");
  });

  it("treats non-positive prices as empty (WHI-794)", () => {
    assert.equal(fmtPrice("0"), "—");
    assert.equal(fmtPrice("0.0000"), "—");
    assert.equal(fmtPrice(-1), "—");
  });
});

describe("isRealUnderlyingPrint", () => {
  it("rejects zero price and epoch as_of (WHI-794)", () => {
    assert.equal(isRealUnderlyingPrint("114.72", 1_700_000_000_000), true);
    assert.equal(isRealUnderlyingPrint("0", 0), false);
    assert.equal(isRealUnderlyingPrint("100", 0), false);
    assert.equal(isRealUnderlyingPrint(null, 1), false);
  });
});

describe("fmtSignedBps", () => {
  it("adds + for positive", () => {
    assert.equal(fmtSignedBps("12.3"), "+12.3");
    assert.equal(fmtSignedBps("-1.5"), "-1.5");
    assert.equal(fmtSignedBps(null), "—");
  });
});

describe("fmtNotional", () => {
  it("uses K/M suffixes", () => {
    assert.equal(fmtNotional("1500"), "1.5K");
    assert.equal(fmtNotional("2500000"), "2.50M");
    assert.equal(fmtNotional("42"), "42");
  });
});

describe("fmtReferenceSize (WHI-966)", () => {
  it("formats config reference size or falls back", () => {
    assert.equal(fmtReferenceSize("1000"), "$1,000");
    assert.equal(fmtReferenceSize(null), "reference size");
    assert.equal(fmtReferenceSize(""), "reference size");
  });
});

describe("fmtUsd / usdTone", () => {
  it("signs and tones PnL", () => {
    assert.equal(fmtUsd("1.5"), "+1.50");
    assert.equal(fmtUsd("-0.25"), "-0.25");
    assert.equal(fmtUsd(null), "—");
    assert.equal(usdTone("-1"), "neg");
    assert.equal(usdTone("2"), "pos");
  });
});

describe("fmtDirection / session / tone", () => {
  it("defaults to Bybit⇄Fluxion short codes", () => {
    assert.equal(fmtDirection("buy_fluxion_sell_bybit"), "F→B");
    assert.equal(fmtDirection("buy_bybit_sell_fluxion"), "B→F");
    assert.equal(fmtDirection(null), "—");
    assert.equal(fmtSession("open"), "OPEN");
    assert.equal(fmtSession("closed"), "CLOSED");
    assert.equal(bpsTone("3"), "pos");
    assert.equal(bpsTone("-1"), "neg");
    assert.equal(bpsTone(null), "empty");
  });

  it("is market-aware for binance-pancake (WHI-780)", () => {
    const bp = { cex: "binance", dex: "pancake" };
    assert.equal(fmtDirection("buy_fluxion_sell_bybit", bp), "P→B");
    assert.equal(fmtDirection("buy_bybit_sell_fluxion", bp), "B→P");
    assert.equal(fmtDirection("buy_fluxion_sell_bybit", null, "binance-pancake"), "P→B");
    assert.equal(fmtDirection("buy_bybit_sell_fluxion", null, "binance-pancake"), "B→P");
  });

  it("keeps F→B on bybit-fluxion venues", () => {
    const bf = { cex: "bybit", dex: "fluxion" };
    assert.equal(fmtDirection("buy_fluxion_sell_bybit", bf), "F→B");
    assert.equal(fmtDirection("buy_bybit_sell_fluxion", bf), "B→F");
    assert.equal(fmtDirection("buy_fluxion_sell_bybit", null, "bybit-fluxion"), "F→B");
  });

  it("tooltips use full venue words without wire-enum leakage", () => {
    assert.equal(
      fmtDirectionTitle("buy_fluxion_sell_bybit", { cex: "binance", dex: "pancake" }),
      "Buy Pancake, sell Binance",
    );
    assert.equal(
      fmtDirectionTitle("buy_bybit_sell_fluxion", { cex: "bybit", dex: "fluxion" }),
      "Buy Bybit, sell Fluxion",
    );
    assert.equal(
      (fmtDirectionTitle("buy_fluxion_sell_bybit", { cex: "binance", dex: "pancake" }) ??
        "").includes("fluxion"),
      false,
    );
  });

  it("venuesFromMarketId / venue labels", () => {
    assert.deepEqual(venuesFromMarketId("binance-pancake"), {
      cex: "binance",
      dex: "pancake",
    });
    assert.deepEqual(venuesFromMarketId("bybit-fluxion"), {
      cex: "bybit",
      dex: "fluxion",
    });
    assert.equal(venueCode("pancake"), "P");
    assert.equal(venueLabel("fluxion"), "Fluxion");
    assert.match(
      directionToggleLabel("buy_fluxion_sell_bybit", { cex: "binance", dex: "pancake" }),
      /P→B \(buy Pancake\)/,
    );
  });
});

describe("shortAddr / explorer", () => {
  it("shortens 0x addresses", () => {
    assert.equal(
      shortAddr("0xabcdef0123456789abcdef0123456789abcdef01"),
      "0xabcdef…ef01",
    );
    assert.equal(shortAddr(null), "—");
    assert.equal(shortAddr("0xabc"), "0xabc");
  });

  it("builds mantle explorer tx urls", () => {
    assert.equal(
      explorerTxUrl("0xdeadbeef"),
      "https://mantlescan.xyz/tx/0xdeadbeef",
    );
    assert.equal(
      explorerTxUrl("deadbeef"),
      "https://mantlescan.xyz/tx/0xdeadbeef",
    );
    assert.equal(explorerTxUrl(null), null);
  });
});

describe("fmtPct / totalWearBps", () => {
  it("formats fractions and sums wear", () => {
    assert.equal(fmtPct(0.123), "12.3%");
    assert.equal(fmtPct(null), "—");
    assert.equal(
      totalWearBps({
        bybit_taker_bps: "10",
        fluxion_fee_bps: "5",
        bybit_slip_bps: "1",
        fluxion_slip_bps: "2",
        gas_bps: "0.5",
        basis_bps: "0",
        withdrawal_fee_bps: "1.5",
      }),
      "20",
    );
    // Missing withdrawal line defaults to 0 (back-compat).
    assert.equal(
      totalWearBps({
        bybit_taker_bps: "10",
        fluxion_fee_bps: "5",
        bybit_slip_bps: "1",
        fluxion_slip_bps: "2",
        gas_bps: "0.5",
        basis_bps: "0",
      }),
      "18.5",
    );
  });
});

describe("fmtVolumeRatio / fmtUtcHm (WHI-777)", () => {
  it("formats compact multipliers", () => {
    assert.equal(fmtVolumeRatio("12.34"), "12.3×");
    assert.equal(fmtVolumeRatio("150"), "150×");
    assert.equal(fmtVolumeRatio("2500"), "2.5K×");
    assert.equal(fmtVolumeRatio(null), "—");
  });

  it("renders UTC HH:MM for truncated labels", () => {
    assert.equal(fmtUtcHm(Date.UTC(2024, 0, 1, 12, 34, 0)), "12:34");
  });
});
