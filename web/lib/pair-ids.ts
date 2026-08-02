import fs from "node:fs";
import path from "node:path";

import { KNOWN_MARKETS } from "./markets";

/**
 * Build-time pair id list from a market inventory YAML
 * (`config/markets/{id}.yaml` — M7-2 / WHI-771).
 * Used by generateStaticParams for Next static export of
 * `/m/{market}/pair/{id}/` (and legacy `/pair/{id}/`).
 * Do not retype the inventory here.
 */
export function loadPairIdsFromConfig(marketId = "bybit-fluxion"): string[] {
  const candidates = [
    path.join(process.cwd(), "config", "markets", `${marketId}.yaml`),
    path.join(process.cwd(), "..", "config", "markets", `${marketId}.yaml`),
  ];
  // Legacy path only for the original Bybit market.
  if (marketId === "bybit-fluxion") {
    candidates.push(
      path.join(process.cwd(), "config", "pairs.yaml"),
      path.join(process.cwd(), "..", "config", "pairs.yaml"),
    );
  }
  const yamlPath = candidates.find((p) => fs.existsSync(p));
  if (!yamlPath) {
    throw new Error(
      `market inventory not found for ${marketId} (cwd=${process.cwd()}); ` +
        `expected config/markets/${marketId}.yaml`,
    );
  }
  const text = fs.readFileSync(yamlPath, "utf8");
  // Walk the `pairs:` list body (top-level or nested under `inventory:`).
  const ids: string[] = [];
  let inPairs = false;
  for (const line of text.split("\n")) {
    if (/^\s*pairs:\s*$/.test(line)) {
      inPairs = true;
      continue;
    }
    if (inPairs) {
      // Top-level key or inventory sibling ends the pairs block.
      if (/^[a-zA-Z_]/.test(line)) break;
      if (/^\s{2}[a-zA-Z_#]/.test(line)) break;
      const m = /^\s+- id:\s+(\S+)\s*$/.exec(line);
      if (m) ids.push(m[1]);
    }
  }
  if (ids.length === 0) {
    throw new Error(`no pair ids parsed from ${yamlPath}`);
  }
  return ids;
}

/** All static params for /m/{market}/pair/{pairId}/. */
export function loadAllMarketPairParams(): { market: string; pairId: string }[] {
  const out: { market: string; pairId: string }[] = [];
  for (const m of KNOWN_MARKETS) {
    for (const pairId of loadPairIdsFromConfig(m.id)) {
      out.push({ market: m.id, pairId });
    }
  }
  return out;
}

export function loadMarketIds(): string[] {
  return KNOWN_MARKETS.map((m) => m.id);
}
