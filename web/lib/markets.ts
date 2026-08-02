/**
 * Multi-market helpers (WHI-774 / M7-5).
 * Market ids match config/markets/{id}.yaml (bybit-fluxion, binance-pancake).
 */

export const DEFAULT_MARKET_ID = "bybit-fluxion";

/** Static market cards for the switcher when /api/markets is still loading. */
export type MarketCard = {
  id: string;
  display_name: string;
  short_label: string;
  has_rfq: boolean;
};

/**
 * Build-time known markets (config/markets). Keep in lockstep with
 * config/markets/*.yaml — Web static export needs these for generateStaticParams.
 */
export const KNOWN_MARKETS: readonly MarketCard[] = [
  {
    id: "bybit-fluxion",
    display_name: "Bybit ⇄ Fluxion",
    short_label: "Bybit",
    has_rfq: true,
  },
  {
    id: "binance-pancake",
    display_name: "Binance ⇄ PancakeSwap",
    short_label: "Binance",
    has_rfq: false,
  },
] as const;

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
