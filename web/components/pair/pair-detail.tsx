"use client";

/**
 * Full pair detail (WHI-759 / WHI-774): header quotes, spread chart, trade stream,
 * edge stats + cost waterfall, attribution panel. Polls market-scoped API.
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";

import { AttributionPanel } from "@/components/pair/attribution-panel";
import { EdgeStatsPanel } from "@/components/pair/edge-panel";
import { MmPanel } from "@/components/pair/mm-panel";
import { SpreadChart } from "@/components/pair/spread-chart";
import { TradeStream } from "@/components/pair/trade-stream";
import { Badge } from "@/components/ui/badge";
import { EmptyPanel } from "@/components/ui/empty-panel";
import { fetchJson } from "@/lib/api";
import {
  bpsTone,
  fmtDirection,
  fmtNotional,
  fmtPrice,
  fmtSession,
  fmtSignedBps,
} from "@/lib/format";
import {
  marketAccumulatingMessage,
  marketApiHealthPath,
  marketApiPairPath,
  marketCard,
} from "@/lib/markets";
import { mmActiveLabel, mmActiveTitle } from "@/lib/mm";
import type {
  HealthResponse,
  PairDetailResponse,
  SessionKind,
} from "@/lib/types";
import { cn } from "@/lib/cn";

/** Fallback until /api/health returns poll_interval_s (config/api.yaml default). */
const DEFAULT_POLL_MS = 2000;

type Props = {
  marketId: string;
  pairId: string;
};

export function PairDetail({ marketId, pairId }: Props) {
  const [data, setData] = useState<PairDetailResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pollMs, setPollMs] = useState(DEFAULT_POLL_MS);
  const [hasRfq, setHasRfq] = useState(
    () => marketCard(marketId)?.has_rfq ?? true,
  );
  const [accumulating, setAccumulating] = useState(false);
  const [displayName, setDisplayName] = useState(
    () => marketCard(marketId)?.display_name ?? marketId,
  );

  const refresh = useCallback(async () => {
    try {
      const d = await fetchJson<PairDetailResponse>(
        marketApiPairPath(marketId, pairId),
      );
      setData(d);
      setErr(null);
      if (d.has_rfq != null) setHasRfq(d.has_rfq);
      if (d.display_name) setDisplayName(d.display_name);
      setAccumulating(d.data_status === "accumulating");
    } catch (e) {
      // Keep last good snapshot so panels do not flash empty on a blip.
      const msg = e instanceof Error ? e.message : String(e);
      setErr(msg);
      if (/503|accumulat/i.test(msg)) {
        setAccumulating(true);
      }
    }
  }, [marketId, pairId]);

  // poll_interval_s + market metadata — fetch once per market, not every tick.
  useEffect(() => {
    let cancelled = false;
    setData(null);
    setErr(null);
    setAccumulating(false);
    setHasRfq(marketCard(marketId)?.has_rfq ?? true);
    setDisplayName(marketCard(marketId)?.display_name ?? marketId);
    void (async () => {
      try {
        const h = await fetchJson<HealthResponse>(
          marketApiHealthPath(marketId),
        );
        if (cancelled) return;
        if (h.poll_interval_s && h.poll_interval_s > 0) {
          setPollMs(Math.round(h.poll_interval_s * 1000));
        }
        if (h.has_rfq != null) setHasRfq(h.has_rfq);
        if (h.display_name) setDisplayName(h.display_name);
        if (h.data_status === "accumulating") setAccumulating(true);
      } catch {
        // Keep DEFAULT_POLL_MS.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [marketId]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => {
      void refresh();
    }, pollMs);
    return () => window.clearInterval(id);
  }, [refresh, pollMs]);

  if (accumulating && !data) {
    return (
      <EmptyPanel
        variant="solid"
        message={marketAccumulatingMessage(displayName)}
        className="py-10"
      />
    );
  }

  if (!data && err) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive-foreground">
        {err}
      </div>
    );
  }

  if (!data) {
    return (
      <p className="text-xs text-muted-foreground">Loading {pairId}…</p>
    );
  }

  const o = data.overview;
  const session: SessionKind = data.session_now;

  return (
    <div className="space-y-4">
      {err && (
        <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-1.5 text-[11px] text-warning">
          Refresh failed — showing last snapshot. {err}
        </div>
      )}

      {/* Header quote strip */}
      <section className="rounded-md border border-border bg-card px-3 py-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold">{data.name}</span>
          <span className="text-xs text-muted-foreground">{data.pair_id}</span>
          <Badge variant={session === "open" ? "open" : "closed"}>
            {fmtSession(session)}
          </Badge>
          {data.low_liquidity && <Badge variant="muted">low-liq</Badge>}
          {o.stale && <Badge variant="warning">stale</Badge>}
          {o.mm_active === "active" && (
            <Badge variant="mm" title={mmActiveTitle(o.mm_active)}>
              {mmActiveLabel(o.mm_active)}
            </Badge>
          )}
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-3 lg:grid-cols-4">
          <Field label="Bybit mid" value={fmtPrice(o.bybit_mid)} />
          <Field label="AMM mid" value={fmtPrice(o.amm_mid)} />
          {hasRfq && (
            <Field
              label="RFQ b/s"
              value={`${fmtPrice(o.rfq_buy)} / ${fmtPrice(o.rfq_sell)}`}
            />
          )}
          <Field
            label="AMM bps"
            value={fmtSignedBps(o.amm_spread_bps)}
            tone={bpsTone(o.amm_spread_bps)}
          />
          {hasRfq && (
            <Field
              label="RFQ bps"
              value={fmtSignedBps(o.rfq_spread_bps)}
              tone={bpsTone(o.rfq_spread_bps)}
            />
          )}
          <Field
            label="Net edge"
            value={fmtSignedBps(o.net_edge_bps)}
            tone={bpsTone(o.net_edge_bps)}
          />
          <Field label="Direction" value={fmtDirection(o.net_edge_direction)} />
          <Field label="Venue" value={o.net_edge_venue ?? "—"} />
          <Field
            label="Vol / trades 24h"
            value={`${fmtNotional(o.volume_24h)} / ${o.trades_24h}`}
          />
        </dl>
      </section>

      <Panel
        title="Spread history"
        subtitle={
          hasRfq
            ? "AMM + RFQ vs Bybit mid · session bands"
            : "AMM vs CEX mid · session bands"
        }
      >
        <SpreadChart points={data.spread_series} showRfq={hasRfq} />
      </Panel>

      <Panel
        title={hasRfq ? "Fluxion fills" : "DEX fills"}
        subtitle={`latest ${data.trades.length}`}
      >
        <TradeStream trades={data.trades} />
      </Panel>

      <Panel title="Arbitrage space" subtitle="paper edge · wear · PnL v2 buckets">
        <EdgeStatsPanel
          amm={data.edge_amm}
          rfq={data.edge_rfq}
          pnl={data.pnl_v2}
          hasRfq={hasRfq}
        />
      </Panel>

      <Panel title="Attribution" subtitle="M4 mechanism + labeled addresses">
        <AttributionPanel
          attribution={data.attribution}
          addressPanel={data.address_panel}
          detail={{
            arb_bot_trade_share: data.arb_bot_trade_share,
            price_keeper_trade_share: data.price_keeper_trade_share,
            rfq_mechanism_share: data.rfq_mechanism_share,
          }}
        />
      </Panel>

      <Panel
        title="Market makers"
        subtitle="inventory curves · rebalance timeline"
      >
        <MmPanel marketId={marketId} pairId={pairId} pollMs={pollMs} />
      </Panel>

      <p className="text-[10px] text-muted-foreground">
        Poll every {pollMs / 1000}s · generated{" "}
        {new Date(data.generated_ts_ms).toLocaleTimeString()} · market{" "}
        <code className="text-foreground">{marketId}</code>
        {" · "}
        <code className="text-foreground">
          /api/{marketId}/pairs/{"{id}"}
        </code>
      </p>
    </div>
  );
}

function Panel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-md border border-border bg-card/30 px-3 py-3">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xs font-semibold tracking-wide uppercase text-foreground">
          {title}
        </h2>
        {subtitle && (
          <span className="text-[10px] text-muted-foreground">{subtitle}</span>
        )}
      </div>
      {children}
    </section>
  );
}

function Field({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "pos" | "neg" | "flat" | "empty";
}) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "tabular-nums text-foreground",
          tone === "pos" && "text-positive",
          tone === "neg" && "text-negative",
          tone === "empty" && "text-muted-foreground",
        )}
      >
        {value}
      </dd>
    </div>
  );
}
