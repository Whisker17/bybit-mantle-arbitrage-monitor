import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { loadPairIdsFromConfig } from "./pair-ids";

describe("loadPairIdsFromConfig", () => {
  it("parses the 11 monitor pair ids from config/pairs.yaml", () => {
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
});
