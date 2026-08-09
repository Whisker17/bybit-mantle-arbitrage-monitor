import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { apiBase, apiBaseLabel, fetchJson } from "./api";

const originalFetch = globalThis.fetch;
const originalEnv = process.env.NEXT_PUBLIC_API_BASE;

afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalEnv === undefined) {
    delete process.env.NEXT_PUBLIC_API_BASE;
  } else {
    process.env.NEXT_PUBLIC_API_BASE = originalEnv;
  }
});

describe("apiBase", () => {
  it("returns empty string when NEXT_PUBLIC_API_BASE is unset (same-origin)", () => {
    delete process.env.NEXT_PUBLIC_API_BASE;
    assert.equal(apiBase(), "");
    assert.equal(apiBaseLabel(), "same-origin");
  });

  it("strips trailing slash from NEXT_PUBLIC_API_BASE", () => {
    process.env.NEXT_PUBLIC_API_BASE = "http://127.0.0.1:8010/";
    assert.equal(apiBase(), "http://127.0.0.1:8010");
    assert.equal(apiBaseLabel(), "http://127.0.0.1:8010");
  });
});

describe("fetchJson", () => {
  it("names the resolved base on network failure (explicit base)", async () => {
    process.env.NEXT_PUBLIC_API_BASE = "http://127.0.0.1:8010";
    globalThis.fetch = async () => {
      throw new TypeError("Failed to fetch");
    };
    await assert.rejects(
      () => fetchJson("/api/health"),
      (err: unknown) => {
        assert.ok(err instanceof Error);
        assert.match(err.message, /cannot reach API at http:\/\/127\.0\.0\.1:8010/);
        assert.match(err.message, /tunnel down or wrong -L port/);
        assert.doesNotMatch(err.message, /Failed to fetch/);
        return true;
      },
    );
  });

  it("names same-origin on network failure when base is empty", async () => {
    delete process.env.NEXT_PUBLIC_API_BASE;
    globalThis.fetch = async () => {
      throw new TypeError("Failed to fetch");
    };
    await assert.rejects(
      () => fetchJson("/api/health"),
      (err: unknown) => {
        assert.ok(err instanceof Error);
        assert.match(err.message, /cannot reach API at same-origin/);
        assert.match(err.message, /tunnel down or API process stopped/);
        return true;
      },
    );
  });

  it("keeps HTTP status message on non-2xx", async () => {
    delete process.env.NEXT_PUBLIC_API_BASE;
    globalThis.fetch = async () =>
      new Response("nope", { status: 503, statusText: "Service Unavailable" });
    await assert.rejects(
      () => fetchJson("/api/health"),
      (err: unknown) => {
        assert.ok(err instanceof Error);
        assert.equal(err.message, "/api/health → HTTP 503");
        return true;
      },
    );
  });

  it("returns parsed JSON on 2xx", async () => {
    delete process.env.NEXT_PUBLIC_API_BASE;
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    const body = await fetchJson<{ ok: boolean }>("/api/health");
    assert.deepEqual(body, { ok: true });
  });
});
