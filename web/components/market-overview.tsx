"use client";

/**
 * Market-scoped overview table (WHI-758 / WHI-766 / WHI-774 / WHI-791).
 * Polls /api/{market}/pairs + health; RFQ columns hidden when has_rfq is false.
 * Default view is Top-N by the active sort key; expand to the full list.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { MarketSwitcher } from "@/components/market-switcher";
import { OverviewControls } from "@/components/overview-controls";
import { PairsTable } from "@/components/pairs-table";
import { StaleBanner } from "@/components/stale-banner";
import { StatusBar } from "@/components/status-bar";
import { Button } from "@/components/ui/button";
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
  applyTopN,
  buildOverviewSearch,
  defaultSortDesc,
  filterRows,
  isSortKey,
  parseOverviewSearch,
  sortKeyLabel,
  sortRows,
  topNSummary,
  TOP_N_DEFAULT,
  type OverviewUrlState,
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

/**
 * Apply shareable `?sort=&desc=&all=` into state setters.
 * Returns whether the URL supplied an explicit sort key (API default skipped).
 */
function applyUrlState(
  fromUrl: OverviewUrlState,
  set: {
    sortKey: (k: SortKey) => void;
    sortDesc: (d: boolean) => void;
    showAll: (v: boolean) => void;
  },
): boolean {
  if (fromUrl.sortKey) {
    set.sortKey(fromUrl.sortKey);
    if (fromUrl.sortDesc !== undefined) {
      set.sortDesc(fromUrl.sortDesc);
    }
  }
  if (fromUrl.showAll !== undefined) {
    set.showAll(fromUrl.showAll);
  }
  return fromUrl.sortKey != null;
}

export function MarketOverview({ marketId }: Props) {
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [markets, setMarkets] = useState<MarketsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pairsErr, setPairsErr] = useState<string | null>(null);
  const [pollMs, setPollMs] = useState(DEFAULT_POLL_MS);

  const [sortKey, setSortKey] = useState<SortKey>("net_edge");
  const [sortDesc, setSortDesc] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const [query, setQuery] = useState("");
  const [hideLowLiquidity, setHideLowLiquidity] = useState(false);
  const [hideStale, setHideStale] = useState(false);
  /**
   * Sort is intentional (share URL or API default applied) — safe to write
   * shareable query params. Also set on operator sort / expand so early
   * expand before first pairs poll still stamps `?all=1`.
   */
  const sortReady = useRef(false);
  /**
   * When URL hydration schedules setState, the URL-write effect in the same
   * commit still sees the previous render's sortKey. Skip one write so we do
   * not flash `?sort=net_edge` over a shared `?sort=tvl_usd`.
   */
  const skipUrlWriteOnce = useRef(false);

  // Mount + market switch: re-read share URL; otherwise wait for API default.
  useEffect(() => {
    sortReady.current = false;
    skipUrlWriteOnce.current = false;
    setOverview(null);
    setHealth(null);
    setPairsErr(null);
    setErr(null);
    setQuery("");
    setShowAll(false);

    if (typeof window === "undefined") return;
    const fromUrl = parseOverviewSearch(window.location.search);
    const hasSortKey = applyUrlState(fromUrl, {
      sortKey: setSortKey,
      sortDesc: setSortDesc,
      showAll: setShowAll,
    });
    if (hasSortKey) {
      sortReady.current = true;
      skipUrlWriteOnce.current = true;
    }
  }, [marketId]);

  // Keep shareable query params in sync (replaceState — no history spam).
  // Wait until sort is intentional so a clean load does not stamp the client
  // default (?sort=net_edge) before /api/pairs reports the market default.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!sortReady.current) return;
    if (skipUrlWriteOnce.current) {
      skipUrlWriteOnce.current = false;
      return;
    }
    const next = buildOverviewSearch({ sortKey, sortDesc, showAll });
    const url = `${window.location.pathname}${next}`;
    if (url !== `${window.location.pathname}${window.location.search}`) {
      window.history.replaceState(null, "", url);
    }
  }, [sortKey, sortDesc, showAll, marketId]);

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
      if (!sortReady.current) {
        if (isSortKey(o.sort_key)) {
          setSortKey(o.sort_key);
        }
        setSortDesc(Boolean(o.sort_desc));
        sortReady.current = true;
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

  const sortedRows = useMemo(() => {
    const base = overview?.rows ?? [];
    const filtered = filterRows(base, {
      query,
      hideLowLiquidity,
      hideStale,
    });
    return sortRows(filtered, sortKey, sortDesc);
  }, [overview, query, hideLowLiquidity, hideStale, sortKey, sortDesc]);

  const topView = useMemo(
    () =>
      applyTopN(sortedRows, sortKey, {
        n: TOP_N_DEFAULT,
        showAll,
      }),
    [sortedRows, sortKey, showAll],
  );

  const emptyMessage = useMemo(() => {
    if (topView.presentCount === 0 && topView.totalCount > 0) {
      const label = sortKeyLabel(sortKey);
      return `No pairs with ${label} data yet — n/a rows trail. Expand to see all ${topView.totalCount}.`;
    }
    return "No pairs match the current filter.";
  }, [topView, sortKey]);

  const markSortReady = useCallback(() => {
    sortReady.current = true;
  }, []);

  const handleSort = useCallback(
    (key: SortKey) => {
      markSortReady();
      if (key === sortKey) {
        // Direction flip keeps expand state.
        setSortDesc((d) => !d);
      } else {
        setSortKey(key);
        setSortDesc(defaultSortDesc(key));
        // New sort key rebuilds the Top-N set — collapse so "click CEX Vol →
        // Top 10 by CEX Vol" is the default path (spec).
        setShowAll(false);
      }
    },
    [sortKey, markSortReady],
  );

  const setSortKeyTouched = useCallback(
    (k: SortKey) => {
      markSortReady();
      setSortKey(k);
      setSortDesc(defaultSortDesc(k));
      setShowAll(false);
    },
    [markSortReady],
  );

  const toggleSortDir = useCallback(() => {
    markSortReady();
    setSortDesc((d) => !d);
  }, [markSortReady]);

  const toggleShowAll = useCallback(() => {
    // Ensure URL sync runs even if expand happens before first pairs poll.
    markSortReady();
    setShowAll((v) => !v);
  }, [markSortReady]);

  return (
    <main className="mx-auto max-w-[1600px] px-3 py-3 sm:px-4">
      <MarketSwitcher marketId={marketId} markets={markets?.markets} />

      <StatusBar
        health={health}
        overview={overview}
        pollMs={pollMs}
        rowCount={overview?.rows.length ?? 0}
        // Filtered universe (not Top-N window) — footer owns the Top-N count.
        filteredCount={topView.totalCount}
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
            rows={topView.rows}
            sortKey={sortKey}
            sortDesc={sortDesc}
            onSort={handleSort}
            marketId={marketId}
            hasRfq={hasRfq}
            venues={venues}
            emptyMessage={emptyMessage}
          />

          {(topView.isTruncated || topView.showAll) &&
            topView.totalCount > 0 && (
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-card px-3 py-2 text-[11px] text-muted-foreground">
                <span className="tabular-nums text-foreground/90">
                  {topNSummary(topView, sortKey)}
                </span>
                <Button
                  type="button"
                  variant="outline"
                  className="h-7 text-[11px]"
                  onClick={toggleShowAll}
                  aria-expanded={topView.showAll}
                >
                  {topView.showAll
                    ? `Collapse to Top ${TOP_N_DEFAULT}`
                    : `Show all ${topView.totalCount} pairs`}
                </Button>
              </div>
            )}
        </>
      )}

      <p className="mt-3 text-[10px] text-muted-foreground">
        {hasRfq
          ? "Prices are de-multiplied CEX L1 vs DEX AMM/RFQ. Net edge is AMM-only at the reference notional (see status bar). "
          : "Prices are CEX L1 vs AMM (this market has no RFQ). Net edge is AMM-only at the reference notional. "}
        Overview defaults to Top {TOP_N_DEFAULT} by the active sort column
        (header click cycles sort; n/a values sort last and never fill Top-N).
        Bucket PnL is PnL v2 optimal cash-flow (hover for direction &amp; size;
        &quot;no depth&quot; when the journal has no depth curve). Row opens pair
        detail. Market selection is the URL path{" "}
        <code className="text-foreground">/m/{"{market}"}/</code>; sort state is
        in the query string for sharing.
      </p>
    </main>
  );
}
