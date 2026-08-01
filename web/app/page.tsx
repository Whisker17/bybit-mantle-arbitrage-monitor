"use client";

/**
 * Overview table (WHI-758): full TUI-parity columns + status / stale banner,
 * client sort/filter, 2s poll, row → /pair/{id}/. Bucket PnL column is a
 * placeholder until WHI-756 lands.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { OverviewControls } from "@/components/overview-controls";
import { PairsTable } from "@/components/pairs-table";
import { StaleBanner } from "@/components/stale-banner";
import { StatusBar } from "@/components/status-bar";
import { fetchJson } from "@/lib/api";
import {
  defaultSortDesc,
  filterRows,
  isSortKey,
  sortRows,
} from "@/lib/sort";
import type { HealthResponse, OverviewResponse, SortKey } from "@/lib/types";

/** Fallback until /api/health returns poll_interval_s (config/api.yaml default). */
const DEFAULT_POLL_MS = 2000;

export default function HomePage() {
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
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

  const refresh = useCallback(async () => {
    // Independent fetches so a 503 on /api/pairs (missing journal) does not
    // discard a successful /api/health that should drive the yellow stale bar.
    const [hRes, oRes] = await Promise.allSettled([
      fetchJson<HealthResponse>("/api/health"),
      fetchJson<OverviewResponse>("/api/pairs"),
    ]);

    if (hRes.status === "fulfilled") {
      const h = hRes.value;
      setHealth(h);
      if (h.poll_interval_s && h.poll_interval_s > 0) {
        setPollMs(Math.round(h.poll_interval_s * 1000));
      }
      // Hard API error only when health itself is unreachable.
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
      // Keep last overview so the table does not flash empty, but surface a
      // yellow banner so frozen numbers never look live.
      setPairsErr(
        oRes.reason instanceof Error
          ? oRes.reason.message
          : String(oRes.reason),
      );
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => {
      void refresh();
    }, pollMs);
    return () => window.clearInterval(id);
  }, [refresh, pollMs]);

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
      <StatusBar
        health={health}
        overview={overview}
        pollMs={pollMs}
        rowCount={overview?.rows.length ?? 0}
        filteredCount={visibleRows.length}
      />

      <StaleBanner health={health} fetchError={err} pairsError={pairsErr} />

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
      />

      <p className="mt-3 text-[10px] text-muted-foreground">
        Prices are de-multiplied Bybit L1 vs Fluxion AMM/RFQ (USDC). Net edge is
        AMM-only at the reference notional (see status bar). Bucket PnL column
        awaits WHI-756. Row opens pair detail (spread chart, fills, edge,
        attribution). UI primitives follow shadcn-style patterns on Tailwind
        (dark-first desk theme).
      </p>
    </main>
  );
}
