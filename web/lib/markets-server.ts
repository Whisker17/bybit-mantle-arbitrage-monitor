/**
 * Server/build-only market YAML loaders (WHI-774).
 * Import from generateStaticParams and Node tests only — never from
 * `"use client"` modules (no node:fs in the browser bundle).
 */

import fs from "node:fs";
import path from "node:path";

import { KNOWN_MARKET_IDS, type MarketCard } from "./markets";

function marketsDirCandidates(): string[] {
  return [
    path.join(process.cwd(), "config", "markets"),
    path.join(process.cwd(), "..", "config", "markets"),
  ];
}

function parseScalar(line: string): string {
  const raw = line.replace(/#.*$/, "").trim();
  if (
    (raw.startsWith("'") && raw.endsWith("'")) ||
    (raw.startsWith('"') && raw.endsWith('"'))
  ) {
    return raw.slice(1, -1);
  }
  return raw;
}

/** Parse the few top-level market fields we need for static routes / checks. */
export function parseMarketYaml(text: string): MarketCard {
  let id = "";
  let displayName = "";
  let hasRfq = false;
  let cexVenue = "";
  let dexVenue = "";
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
      const vM = /^\s+venue:\s*(.+)$/.exec(line);
      if (vM) dexVenue = parseScalar(vM[1]);
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
  const idParts = id.split("-");
  return {
    id,
    display_name: displayName || id,
    short_label: short,
    has_rfq: hasRfq,
    cex_venue: cexVenue || idParts[0] || id,
    dex_venue: dexVenue || idParts[1] || "",
  };
}

/** Market cards from config/markets/*.yaml (build / test only). */
export function loadKnownMarketsFromDisk(): MarketCard[] {
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
 * Assert KNOWN_MARKET_IDS matches disk so client fallback and static
 * routes stay in lockstep with config/markets.
 */
export function assertKnownMarketIdsMatchDisk(): void {
  const fromDisk = loadKnownMarketsFromDisk().map((m) => m.id).sort();
  const fromConst = [...KNOWN_MARKET_IDS].sort();
  if (fromDisk.join(",") !== fromConst.join(",")) {
    throw new Error(
      `KNOWN_MARKET_IDS (${fromConst.join(",")}) != config/markets ` +
        `(${fromDisk.join(",")}); update web/lib/markets.ts`,
    );
  }
}
