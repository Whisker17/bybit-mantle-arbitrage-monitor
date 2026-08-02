/**
 * Multi-market path helpers + client-safe constants (WHI-774 / M7-5).
 *
 * Pure TS only — no node:fs. Build-time YAML loading lives in
 * `markets-server.ts` (imported only from server generateStaticParams /
 * Node tests). Client components get display/has_rfq from GET /api/markets
 * and fall back to the id-only list below when the API is still loading.
 */

export const DEFAULT_MARKET_ID = "bybit-fluxion";

/**
 * Checked-in market ids (must match config/markets/*.yaml stems).
 * Ids only — no display_name/has_rfq retype; those come from the API.
 * generateStaticParams uses markets-server.ts which reads the YAML directory.
 */
export const KNOWN_MARKET_IDS: readonly string[] = [
  "bybit-fluxion",
  "binance-pancake",
] as const;

export type MarketCard = {
  id: string;
  display_name: string;
  short_label: string;
  has_rfq: boolean;
  cex_venue: string;
};

/** Minimal client fallback cards until /api/markets responds. */
export const KNOWN_MARKETS: readonly MarketCard[] = KNOWN_MARKET_IDS.map(
  (id) => ({
    id,
    display_name: id,
    short_label: id.split("-")[0] ?? id,
    // Safe default: hide RFQ until API confirms has_rfq (binance has none).
    has_rfq: id === "bybit-fluxion",
    cex_venue: id.split("-")[0] ?? id,
  }),
);

export function isKnownMarketId(id: string): boolean {
  return (KNOWN_MARKET_IDS as readonly string[]).includes(id);
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
