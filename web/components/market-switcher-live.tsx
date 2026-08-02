"use client";

/**
 * MarketSwitcher that hydrates labels + health dots from GET /api/markets.
 * Used on server-rendered pages (pair detail) that cannot pass markets props.
 */

import { useEffect, useState } from "react";

import { MarketSwitcher } from "@/components/market-switcher";
import { fetchJson } from "@/lib/api";
import type { MarketsResponse } from "@/lib/types";

type Props = {
  marketId: string;
};

export function MarketSwitcherLive({ marketId }: Props) {
  const [markets, setMarkets] = useState<MarketsResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const m = await fetchJson<MarketsResponse>("/api/markets");
        if (!cancelled) setMarkets(m);
      } catch {
        // Fall back to KNOWN_MARKETS inside MarketSwitcher.
      }
    })();
    const id = window.setInterval(() => {
      void (async () => {
        try {
          const m = await fetchJson<MarketsResponse>("/api/markets");
          if (!cancelled) setMarkets(m);
        } catch {
          // keep last
        }
      })();
    }, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  return <MarketSwitcher marketId={marketId} markets={markets?.markets} />;
}
