"use client";

/**
 * Overview table (WHI-758): full TUI-parity columns + status / stale banner,
 * client sort/filter, 2s poll, row → /pair/{id}/. Bucket PnL column is a
 * placeholder until WHI-756 lands.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { OverviewControls } from "@/components/overview-controls";
import { PairsTable } from "@/components/pairs-table";
import { StaleBanner } from "@/components/stale-banner";
import { StatusBar } from "@/components/status-bar";
import { fetchJson } from "@/lib/api";
import { filterRows, sortRows } from "@/lib/sort";
import type { HealthResponse, OverviewResponse, SortKey } from "@/lib/types";

const DEFAULT_POLL_MS = 2000;

export default function HomePage() {
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pollMs, setPollMs] = useState(DEFAULT_POLL_MS);

  const [sortKey, setSortKey] = useState<SortKey>("net_edge");
  const [sortDesc, setSortDesc] = useState(true);
  const [query, setQuery] = useState("");
  const [hideLowLiquidity, setHideLowLiquidity] = useState(false);
  const [hideStale, setHideStale] = useState(false);
  const [sortHydrated, setSortHydrated] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [h, o] = await Promise.all([
        fetchJson<HealthResponse>("/api/health"),
        fetchJson<OverviewResponse>("/api/pairs"),
      ]);
      setHealth(h);
      setOverview(o);
      setErr(null);
      if (h.poll_interval_s && h.poll_interval_s > 0) {
        setPollMs(Math.round(h.poll_interval_s * 1000));
      }
      // Adopt server default sort once so the panel matches tui.yaml on first paint.
      if (!sortHydrated) {
        const allowed: SortKey[] = [
          "pair_id",
          "net_edge",
          "amm_spread",
          "rfq_spread",
          "bybit_mid",
          "volume_24h",
          "trades_24h",
        ];
        if (allowed.includes(o.sort_key as SortKey)) {
          setSortKey(o.sort_key as SortKey);
        }
        setSortDesc(Boolean(o.sort_desc));
        setSortHydrated(true);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [sortHydrated]);

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
      if (key === sortKey) {
        setSortDesc((d) => !d);
      } else {
        setSortKey(key);
        setSortDesc(true);
      }
    },
    [sortKey],
  );

  return (
    <main className="mx-auto max-w-[1600px] px-3 py-3 sm:px-4">
      <StatusBar
        health={health}
        overview={overview}
        pollMs={pollMs}
        rowCount={overview?.rows.length ?? 0}
        filteredCount={visibleRows.length}
      />

      <StaleBanner health={health} fetchError={err} />

      <OverviewControls
        query={query}
        onQuery={setQuery}
        sortKey={sortKey}
        sortDesc={sortDesc}
        onSortKey={setSortKey}
        onToggleDir={() => setSortDesc((d) => !d)}
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
        awaits WHI-756. Pair detail: WHI-759.
      </p>
    </main>
  );
}
