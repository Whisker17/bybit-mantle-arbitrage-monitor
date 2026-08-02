/**
 * Multi-market helpers (WHI-774 / M7-5).
 * Market cards are loaded from config/markets/*.yaml at module load (build /
 * Node test) — not retyped by hand. Runtime browser code uses the same
 * constants embedded by the Next bundler via the fs read at build time.
 */

import fs from "node:fs";
import path from "node:path";

export const DEFAULT_MARKET_ID = "bybit-fluxion";

export type MarketCard = {
  id: string;
  display_name: string;
  short_label: string;
  has_rfq: boolean;
  cex_venue: string;
};

function marketsDirCandidates(): string[] {
  return [
    path.join(process.cwd(), "config", "markets"),
    path.join(process.cwd(), "..", "config", "markets"),
  ];
}

function parseScalar(line: string): string {
  // Strip inline comments and surrounding quotes from a YAML scalar value.
  const raw = line.replace(/#.*$/, "").trim();
  if (
    (raw.startsWith("'") && raw.endsWith("'")) ||
    (raw.startsWith('"') && raw.endsWith('"'))
  ) {
    return raw.slice(1, -1);
  }
  return raw;
}

/** Parse the few top-level market fields we need for static routes / switcher. */
export function parseMarketYaml(text: string): MarketCard {
  let id = "";
  let displayName = "";
  let hasRfq = false;
  let cexVenue = "";
  let inCex = false;
  let inDex = false;
  for (const line of text.split("\n")) {
    if (/^cex:\s*$/.test(line)) {
      inCex = true;
      inDex = false;
      continue;
    }
    if (/^dex:\s*$/.test(line)) {
      inDex = true;
      inCex = false;
      continue;
    }
    if (/^[a-zA-Z_]/.test(line) && !/^(cex|dex):/.test(line)) {
      inCex = false;
      inDex = false;
    }
    const idM = /^id:\s*(.+)$/.exec(line);
    if (idM && !id) id = parseScalar(idM[1]);
    const dnM = /^display_name:\s*(.+)$/.exec(line);
    if (dnM) displayName = parseScalar(dnM[1]);
    if (inCex) {
      const vM = /^\s+venue:\s*(.+)$/.exec(line);
      if (vM) cexVenue = parseScalar(vM[1]);
    }
    if (inDex) {
      const rM = /^\s+has_rfq:\s*(true|false)\s*$/.exec(line);
      if (rM) hasRfq = rM[1] === "true";
    }
  }
  if (!id) {
    throw new Error("market yaml missing id");
  }
  const short =
    cexVenue === "bybit"
      ? "Bybit"
      : cexVenue === "binance"
        ? "Binance"
        : id.split("-")[0] ?? id;
  return {
    id,
    display_name: displayName || id,
    short_label: short,
    has_rfq: hasRfq,
    cex_venue: cexVenue || id,
  };
}

function loadKnownMarketsFromDisk(): MarketCard[] {
  const dir = marketsDirCandidates().find((d) => fs.existsSync(d));
  if (!dir) {
    throw new Error(
      `config/markets not found (cwd=${process.cwd()}); expected checked-in market YAMLs`,
    );
  }
  const files = fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".yaml"))
    .sort();
  if (files.length === 0) {
    throw new Error(`no market yaml files in ${dir}`);
  }
  return files.map((f) => {
    const text = fs.readFileSync(path.join(dir, f), "utf8");
    return parseMarketYaml(text);
  });
}

/**
 * Build-time known markets from config/markets/*.yaml.
 * Next static export and unit tests load this at import time.
 */
export const KNOWN_MARKETS: readonly MarketCard[] = loadKnownMarketsFromDisk();

export function isKnownMarketId(id: string): boolean {
  return KNOWN_MARKETS.some((m) => m.id === id);
}

export function marketOverviewPath(marketId: string): string {
  return `/m/${encodeURIComponent(marketId)}/`;
}

export function marketPairPath(marketId: string, pairId: string): string {
  return `/m/${encodeURIComponent(marketId)}/pair/${encodeURIComponent(pairId)}/`;
}

export function marketApiPairsPath(marketId: string): string {
  return `/api/${encodeURIComponent(marketId)}/pairs`;
}

export function marketApiHealthPath(marketId: string): string {
  return `/api/${encodeURIComponent(marketId)}/health`;
}

export function marketApiPairPath(marketId: string, pairId: string): string {
  return `/api/${encodeURIComponent(marketId)}/pairs/${encodeURIComponent(pairId)}`;
}

export function marketApiPairMmPath(marketId: string, pairId: string): string {
  return `/api/${encodeURIComponent(marketId)}/pairs/${encodeURIComponent(pairId)}/mm`;
}

/** User-facing empty-state copy when a market journal is not ready yet. */
export function marketAccumulatingMessage(displayName?: string | null): string {
  const name = displayName?.trim() || "This market";
  return `${name} data accumulating — collector journal not ready yet.`;
}

export function marketCard(id: string): MarketCard | undefined {
  return KNOWN_MARKETS.find((m) => m.id === id);
}
