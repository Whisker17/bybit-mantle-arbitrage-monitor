"use client";

/**
 * Market-scoped overview table (WHI-758 / WHI-766 / WHI-774).
 * Polls /api/{market}/pairs + health; RFQ columns hidden when has_rfq is false.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { MarketSwitcher } from "@/components/market-switcher";
import { OverviewControls } from "@/components/overview-controls";
import { PairsTable } from "@/components/pairs-table";
import { StaleBanner } from "@/components/stale-banner";
import { StatusBar } from "@/components/status-bar";
import { EmptyPanel } from "@/components/ui/empty-panel";
import { fetchJson } from "@/lib/api";
import { resolveVenues, type DirectionVenues } from "@/lib/format";
import {
  marketAccumulatingMessage,
  marketApiHealthPath,
  marketApiPairsPath,
  marketCard,
} from "@/lib/markets";
import {
  defaultSortDesc,
  filterRows,
  isSortKey,
  sortRows,
} from "@/lib/sort";
import type {
  HealthResponse,
  MarketsResponse,
  OverviewResponse,
  SortKey,
} from "@/lib/types";

/** Fallback until /api/health returns poll_interval_s (config/api.yaml default). */
const DEFAULT_POLL_MS = 2000;

type Props = {
  marketId: string;
};

export function MarketOverview({ marketId }: Props) {
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [markets, setMarkets] = useState<MarketsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pairsErr, setPairsErr] = useState<string | null>(null);
  const [pollMs, setPollMs] = useState(DEFAULT_POLL_MS);

  const [sortKey, setSortKey] = useState<SortKey>("net_edge");
  const [sortDesc, setSortDesc] = useState(true);
  const [query, setQuery] = useState("");
  const [hideLowLiquidity, setHideLowLiquidity] = useState(false);
  const [hideStale, setHideStale] = useState(false);
  /** Once the operator touches sort, stop adopting server defaults. */
  const sortTouched = useRef(false);
  const sortHydrated = useRef(false);

  // Reset sort hydration when switching markets so each market can apply its default.
  useEffect(() => {
    sortHydrated.current = false;
    sortTouched.current = false;
    setOverview(null);
    setHealth(null);
    setPairsErr(null);
    setErr(null);
    setQuery("");
  }, [marketId]);

  const refresh = useCallback(async () => {
    // Independent fetches so a 503 on pairs does not discard a successful health.
    // /api/markets is NOT on the 2s poll — it is loaded separately (switcher only).
    const [hRes, oRes] = await Promise.allSettled([
      fetchJson<HealthResponse>(marketApiHealthPath(marketId)),
      fetchJson<OverviewResponse>(marketApiPairsPath(marketId)),
    ]);

    if (hRes.status === "fulfilled") {
      const h = hRes.value;
      setHealth(h);
      if (h.poll_interval_s && h.poll_interval_s > 0) {
        setPollMs(Math.round(h.poll_interval_s * 1000));
      }
      setErr(null);
    } else {
      setErr(
        hRes.reason instanceof Error
          ? hRes.reason.message
          : String(hRes.reason),
      );
    }

    if (oRes.status === "fulfilled") {
      const o = oRes.value;
      setOverview(o);
      setPairsErr(null);
      if (!sortHydrated.current && !sortTouched.current) {
        if (isSortKey(o.sort_key)) {
          setSortKey(o.sort_key);
        }
        setSortDesc(Boolean(o.sort_desc));
        sortHydrated.current = true;
      }
    } else {
      setPairsErr(
        oRes.reason instanceof Error
          ? oRes.reason.message
          : String(oRes.reason),
      );
    }
  }, [marketId]);

  // Market list for the switcher — once on mount + slow refresh (not every 2s).
  useEffect(() => {
    let cancelled = false;
    const loadMarkets = async () => {
      try {
        const m = await fetchJson<MarketsResponse>("/api/markets");
        if (!cancelled) setMarkets(m);
      } catch {
        // Switcher falls back to KNOWN_MARKETS.
      }
    };
    void loadMarkets();
    const id = window.setInterval(() => {
      void loadMarkets();
    }, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => {
      void refresh();
    }, pollMs);
    return () => window.clearInterval(id);
  }, [refresh, pollMs]);

  const hasRfq =
    overview?.has_rfq ??
    health?.has_rfq ??
    marketCard(marketId)?.has_rfq ??
    true;

  const displayName =
    overview?.display_name ??
    health?.display_name ??
    markets?.markets.find((m) => m.id === marketId)?.display_name ??
    marketCard(marketId)?.display_name ??
    marketId;

  const dataStatus =
    overview?.data_status ??
    health?.data_status ??
    markets?.markets.find((m) => m.id === marketId)?.data_status;

  const accumulating = dataStatus === "accumulating";

  // Group headers + Dir labels use venue names from /api/markets (WHI-780).
  const venues: DirectionVenues = useMemo(() => {
    const m = markets?.markets.find((x) => x.id === marketId);
    return resolveVenues(
      m ? { cex: m.cex_venue, dex: m.dex_venue } : null,
      marketId,
    );
  }, [markets, marketId]);

  const visibleRows = useMemo(() => {
    const base = overview?.rows ?? [];
    const filtered = filterRows(base, {
      query,
      hideLowLiquidity,
      hideStale,
    });
    return sortRows(filtered, sortKey, sortDesc);
  }, [overview, query, hideLowLiquidity, hideStale, sortKey, sortDesc]);

  const handleSort = useCallback(
    (key: SortKey) => {
      sortTouched.current = true;
      if (key === sortKey) {
        setSortDesc((d) => !d);
      } else {
        setSortKey(key);
        setSortDesc(defaultSortDesc(key));
      }
    },
    [sortKey],
  );

  const setSortKeyTouched = useCallback((k: SortKey) => {
    sortTouched.current = true;
    setSortKey(k);
    setSortDesc(defaultSortDesc(k));
  }, []);

  const toggleSortDir = useCallback(() => {
    sortTouched.current = true;
    setSortDesc((d) => !d);
  }, []);

  return (
    <main className="mx-auto max-w-[1600px] px-3 py-3 sm:px-4">
      <MarketSwitcher marketId={marketId} markets={markets?.markets} />

      <StatusBar
        health={health}
        overview={overview}
        pollMs={pollMs}
        rowCount={overview?.rows.length ?? 0}
        filteredCount={visibleRows.length}
        displayName={displayName}
      />

      <StaleBanner health={health} fetchError={err} pairsError={pairsErr} />

      {accumulating ? (
        <EmptyPanel
          variant="solid"
          message={
            overview?.error ?? marketAccumulatingMessage(displayName)
          }
          className="mb-3 py-10"
        />
      ) : (
        <>
          <OverviewControls
            query={query}
            onQuery={setQuery}
            sortKey={sortKey}
            sortDesc={sortDesc}
            onSortKey={setSortKeyTouched}
            onToggleDir={toggleSortDir}
            hideLowLiquidity={hideLowLiquidity}
            onHideLowLiquidity={setHideLowLiquidity}
            hideStale={hideStale}
            onHideStale={setHideStale}
            onRefresh={() => {
              void refresh();
            }}
          />

          <PairsTable
            rows={visibleRows}
            sortKey={sortKey}
            sortDesc={sortDesc}
            onSort={handleSort}
            marketId={marketId}
            hasRfq={hasRfq}
            venues={venues}
          />
        </>
      )}

      <p className="mt-3 text-[10px] text-muted-foreground">
        {hasRfq
          ? "Prices are de-multiplied CEX L1 vs DEX AMM/RFQ. Net edge is AMM-only at the reference notional (see status bar). "
          : "Prices are CEX L1 vs AMM (this market has no RFQ). Net edge is AMM-only at the reference notional. "}
        Bucket PnL is PnL v2 optimal cash-flow (hover for direction &amp; size;
        &quot;no depth&quot; when the journal has no depth curve). Row opens pair
        detail. Market selection is the URL path{" "}
        <code className="text-foreground">/m/{"{market}"}/</code>.
      </p>
    </main>
  );
}
