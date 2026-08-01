/**
 * Lightweight node:test suite for pure format/sort helpers.
 * Run: `npx tsx --test lib/format.test.ts lib/sort.test.ts`
 * (optional; `npm run build` is the gate for static export).
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  bpsTone,
  fmtDirection,
  fmtNotional,
  fmtPrice,
  fmtSession,
  fmtSignedBps,
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
