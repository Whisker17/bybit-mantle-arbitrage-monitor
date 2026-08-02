import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { mmActiveLabel, mmActiveTitle, mmEmptyMessage } from "./mm";

describe("mm helpers", () => {
  it("labels three-state badge", () => {
    assert.equal(mmActiveLabel("active"), "MM");
    assert.equal(mmActiveLabel("inactive"), "—");
    assert.equal(mmActiveLabel("unknown"), "?");
    assert.equal(mmActiveLabel(null), "?");
  });

  it("empty-state copy covers accumulating and no_candidates", () => {
    assert.match(mmEmptyMessage("accumulating"), /accumulat/i);
    assert.match(mmEmptyMessage("no_candidates"), /No market_maker/i);
    assert.equal(mmEmptyMessage("ok"), "");
  });

  it("titles explain badge", () => {
    assert.match(mmActiveTitle("active"), /24h/);
    assert.match(mmActiveTitle("unknown"), /pending/i);
  });
});
