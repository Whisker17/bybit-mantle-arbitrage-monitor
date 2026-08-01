/**
 * Lightweight node:test suite for pure format/sort helpers.
 * Run: `npx tsx --test lib/format.test.ts lib/sort.test.ts`
 * (optional; `npm run build` is the gate for static export).
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  bpsTone,
  explorerTxUrl,
  fmtDirection,
  fmtNotional,
  fmtPct,
  fmtPrice,
  fmtSession,
  fmtSignedBps,
  shortAddr,
  totalWearBps,
} from "./format";

describe("fmtPrice", () => {
  it("formats finite numbers and blanks nulls", () => {
    assert.equal(fmtPrice("100.12345"), "100.1235");
    assert.equal(fmtPrice(null), "—");
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

describe("fmtDirection / session / tone", () => {
  it("maps known enums", () => {
    assert.equal(fmtDirection("buy_fluxion_sell_bybit"), "F→B");
    assert.equal(fmtDirection("buy_bybit_sell_fluxion"), "B→F");
    assert.equal(fmtSession("open"), "OPEN");
    assert.equal(fmtSession("closed"), "CLOSED");
    assert.equal(bpsTone("3"), "pos");
    assert.equal(bpsTone("-1"), "neg");
    assert.equal(bpsTone(null), "empty");
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
      }),
      "18.5",
    );
  });
});
