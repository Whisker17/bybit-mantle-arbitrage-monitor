"use client";

/**
 * Top market selection bar (WHI-774 / M7-5).
 * Selected market is the URL segment /m/{market}/ — no localStorage.
 */

import { Building2, Landmark } from "lucide-react";
import Link from "next/link";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/cn";
import {
  KNOWN_MARKETS,
  marketOverviewPath,
  type MarketCard,
} from "@/lib/markets";
import type { MarketSummary } from "@/lib/types";

type Props = {
  marketId: string;
  /** Live cards from GET /api/markets when available; falls back to KNOWN_MARKETS. */
  markets?: MarketSummary[] | null;
};

const ICONS: Record<string, LucideIcon> = {
  bybit: Landmark,
  binance: Building2,
};

function cardFromSummary(m: MarketSummary): MarketCard {
  return {
    id: m.id,
    display_name: m.display_name,
    short_label:
      m.cex_venue === "binance"
        ? "Binance"
        : m.cex_venue === "bybit"
          ? "Bybit"
          : m.id,
    has_rfq: m.has_rfq,
    cex_venue: m.cex_venue,
  };
}

export function MarketSwitcher({ marketId, markets }: Props) {
  const cards: MarketCard[] =
    markets && markets.length > 0
      ? markets.map(cardFromSummary)
      : [...KNOWN_MARKETS];

  return (
    <nav
      className="mb-2 flex flex-wrap items-center gap-1.5 rounded-md border border-border bg-card/60 px-2 py-1.5"
      aria-label="Market"
    >
      <span className="mr-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        Market
      </span>
      {cards.map((card) => {
        const active = card.id === marketId;
        const summary = markets?.find((m) => m.id === card.id);
        const alive = summary?.health.collector_alive ?? null;
        const Icon = ICONS[card.cex_venue] ?? Landmark;
        // Second half of "CEX ⇄ DEX" display_name for the long label.
        const dexHalf = card.display_name.includes("⇄")
          ? card.display_name.split("⇄")[1]?.trim()
          : "";
        return (
          <Link
            key={card.id}
            href={marketOverviewPath(card.id)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-[11px] font-medium transition-colors",
              active
                ? "bg-primary text-primary-foreground shadow-sm"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            aria-current={active ? "page" : undefined}
            title={card.display_name}
          >
            <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
            <span
              className={cn(
                "inline-block h-1.5 w-1.5 rounded-full",
                alive === true && "bg-positive",
                alive === false && "bg-warning",
                alive === null &&
                  (active
                    ? "bg-primary-foreground/70"
                    : "bg-muted-foreground/50"),
              )}
              aria-hidden
            />
            <span>{card.short_label}</span>
            {dexHalf && (
              <span
                className={cn(
                  "hidden font-normal sm:inline",
                  active
                    ? "text-primary-foreground/80"
                    : "text-muted-foreground/80",
                )}
              >
                ⇄ {dexHalf}
              </span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}
