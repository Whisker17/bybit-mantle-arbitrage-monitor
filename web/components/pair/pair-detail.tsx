"use client";

/**
 * Full pair detail (WHI-759 / WHI-774): header quotes, spread chart, trade stream,
 * edge stats + cost waterfall, attribution panel. Polls market-scoped API.
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";

import { AttributionPanel } from "@/components/pair/attribution-panel";
import { EdgeStatsPanel } from "@/components/pair/edge-panel";
import { MmPanel } from "@/components/pair/mm-panel";
import { PremiumPanelView } from "@/components/pair/premium-panel";
import { SpreadChart } from "@/components/pair/spread-chart";
import { TradeStream } from "@/components/pair/trade-stream";
import { VolumePanel } from "@/components/pair/volume-panel";
import { Badge } from "@/components/ui/badge";
import { EmptyPanel } from "@/components/ui/empty-panel";
import { fetchJson } from "@/lib/api";
import {
  ammQuoteReasonLabel,
  ammQuoteReasonTitle,
  bpsTone,
  cexPremiumBps,
  fmtDirection,
  fmtDirectionTitle,
  fmtNotional,
  fmtOrAmmReason,
  fmtPrice,
  fmtSession,
  fmtSignedBps,
  fmtUnderlyingPriceType,
  isRealUnderlyingPrint,
  resolveVenues,
  type DirectionVenues,
} from "@/lib/format";
import { overviewNetCell } from "@/lib/pnl";
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

function venuesFromCard(marketId: string): DirectionVenues {
  const card = marketCard(marketId);
  return resolveVenues(
    card ? { cex: card.cex_venue, dex: card.dex_venue } : null,
    marketId,
  );
}

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
  const venues = venuesFromCard(marketId);

  const refresh = useCallback(async () => {
    try {
      const d = await fetchJson<PairDetailResponse>(
        marketApiPairPath(marketId, pairId),
      );
      setData(d);
      setErr(null);
      if (d.has_rfq != null) setHasRfq(d.has_rfq);
      if (d.display_name) setDisplayName(d.display_name);
      // Only markets without builders are "accumulating"; missing journals
      // on builder-ready markets stay an error (HTTP 503 → err banner).
      setAccumulating(d.data_status === "accumulating");
    } catch (e) {
      // Keep last good snapshot so panels do not flash empty on a blip.
      const msg = e instanceof Error ? e.message : String(e);
      setErr(msg);
      // If health already said this market is accumulating (no builders),
      // prefer the empty-state panel over a red error box on direct pair URLs.
      // Builder-ready markets with a missing journal stay an error (503).
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

  // Direct deep-link to a pair on an accumulating market: detail 503s with
  // "not yet wired" — prefer the same empty state as overview (not a red box).
  if (
    !data &&
    err &&
    /not yet wired|accumulat/i.test(err)
  ) {
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
  const ammReason = ammQuoteReasonLabel(o.amm_quote_reason);

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
          {o.stale && (
            <Badge
              variant="warning"
              title="No CEX book tick in journal (distinct from quote aged / price stale)"
            >
              no book
            </Badge>
          )}
          {o.mm_active === "active" && (
            <Badge variant="mm" title={mmActiveTitle(o.mm_active)}>
              {mmActiveLabel(o.mm_active)}
            </Badge>
          )}
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-3 lg:grid-cols-4">
          <Field label="Bybit mid" value={fmtPrice(o.bybit_mid)} />
          <Field
            label="AMM mid"
            value={fmtOrAmmReason(
              fmtPrice(o.amm_mid),
              o.amm_mid,
              o.amm_quote_reason,
            )}
            title={ammQuoteReasonTitle(o.amm_quote_reason)}
          />
          {hasRfq && (
            <Field
              label="RFQ b/s"
              value={`${fmtPrice(o.rfq_buy)} / ${fmtPrice(o.rfq_sell)}`}
            />
          )}
          <Field
            label="vs CEX"
            value={fmtOrAmmReason(
              fmtSignedBps(o.amm_spread_bps),
              o.amm_spread_bps,
              o.amm_quote_reason,
            )}
            tone={bpsTone(o.amm_spread_bps)}
            title="AMM mid vs CEX mid (bps)"
          />
          {hasRfq && (
            <Field
              label="RFQ vs CEX"
              value={fmtSignedBps(o.rfq_spread_bps)}
              tone={bpsTone(o.rfq_spread_bps)}
              title="RFQ mid vs CEX mid (bps)"
            />
          )}
          <VsUndField
            label="CEX vs Und"
            privateUnderlying={o.underlying_empty === "private"}
            value={cexPremiumBps(o)}
            title="CEX equity-eq mid vs underlying (bps)"
          />
          <VsUndField
            label="DEX vs Und"
            privateUnderlying={o.underlying_empty === "private"}
            value={o.amm_premium_bps ?? null}
            emptyLabel={ammReason}
            title={
              hasRfq && o.rfq_premium_bps != null
                ? `AMM vs Und ${fmtSignedBps(o.amm_premium_bps)} · RFQ vs Und ${fmtSignedBps(o.rfq_premium_bps)}`
                : "AMM mid vs underlying (bps)"
            }
          />
          {(() => {
            const net = overviewNetCell(
              o,
              ammQuoteReasonTitle(o.amm_quote_reason) ?? null,
            );
            const netValue =
              o.net_edge_bps != null
                ? fmtSignedBps(o.net_edge_bps)
                : (net.emptyLabel ??
                  ammQuoteReasonLabel(o.amm_quote_reason) ??
                  "—");
            return (
              <>
                <Field
                  label="Net @ Q*"
                  value={netValue}
                  tone={bpsTone(o.net_edge_bps)}
                  title={net.title}
                />
                <Field
                  label="Q*"
                  value={
                    o.net_size_usd != null
                      ? `$${fmtNotional(o.net_size_usd)}`
                      : "—"
                  }
                  title="PnL v2 optimal notional for Net / Bucket PnL"
                />
              </>
            );
          })()}
          <Field
            label="Direction"
            value={fmtOrAmmReason(
              fmtDirection(o.net_edge_direction, venues, marketId),
              o.net_edge_direction,
              o.amm_quote_reason,
            )}
            title={
              o.net_edge_direction == null && ammReason
                ? ammQuoteReasonTitle(o.amm_quote_reason)
                : fmtDirectionTitle(o.net_edge_direction, venues, marketId)
            }
          />
          <Field label="Venue" value={o.net_edge_venue ?? "—"} />
          <Field
            label="CEX Vol 24h"
            value={fmtNotional(o.cex_volume_24h)}
          />
          <Field
            label="DEX Vol 24h"
            value={
              o.dex_volume_truncated
                ? `${fmtNotional(o.dex_volume_24h)} *`
                : fmtNotional(o.dex_volume_24h)
            }
          />
          <Field
            label="Underlying"
            value={
              o.underlying_empty === "private"
                ? "n/a private"
                : isRealUnderlyingPrint(
                      o.underlying_price,
                      o.underlying_as_of_ms,
                    )
                  ? `${fmtPrice(o.underlying_price)}${
                      (() => {
                        const lab = fmtUnderlyingPriceType(
                          o.underlying_price_type,
                          o.premium_type_label,
                        );
                        return lab ? ` (${lab})` : "";
                      })()
                    }`
                  : "—"
            }
            title="Reference equity price only — venue premiums live above"
          />
        </dl>
      </section>

      <Panel
        title="Volume compare"
        subtitle="CEX REST 24h vs DEX swaps · open/closed sessions"
      >
        <VolumePanel volume={data.volume_compare} />
      </Panel>

      <Panel
        title="Premium vs underlying"
        subtitle="CEX / AMM / RFQ tokenized mid vs equity reference"
      >
        <PremiumPanelView premium={data.premium} hasRfq={hasRfq} />
      </Panel>

      <Panel
        title="Spread history"
        subtitle={
          hasRfq
            ? "DEX vs CEX · CEX/DEX vs Und · session bands"
            : "AMM vs CEX · CEX/DEX vs Und · session bands"
        }
      >
        <SpreadChart points={data.spread_series} showRfq={hasRfq} />
      </Panel>

      <Panel
        title={hasRfq ? "Fluxion fills" : "DEX fills"}
        subtitle={`latest ${data.trades.length}`}
      >
        <TradeStream trades={data.trades} venues={venues} marketId={marketId} />
      </Panel>

      <Panel title="Arbitrage space" subtitle="paper edge · wear · PnL v2 buckets">
        <EdgeStatsPanel
          amm={data.edge_amm}
          rfq={data.edge_rfq}
          pnl={data.pnl_v2}
          hasRfq={hasRfq}
          venues={venues}
          marketId={marketId}
          referenceSizeUsd={o.reference_size_usd}
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
  title,
}: {
  label: string;
  value: string;
  tone?: "pos" | "neg" | "flat" | "empty";
  title?: string;
}) {
  return (
    <div title={title}>
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

/** Detail header premium field with private-underlying n/a (WHI-783). */
function VsUndField({
  label,
  privateUnderlying,
  value,
  title,
  emptyLabel,
}: {
  label: string;
  privateUnderlying: boolean;
  value: string | null | undefined;
  title: string;
  /** When value is null (e.g. empty pool), show this instead of em-dash. */
  emptyLabel?: string | null;
}) {
  if (privateUnderlying) {
    return (
      <Field
        label={label}
        value="n/a"
        tone="empty"
        title="Private underlying — no premium"
      />
    );
  }
  if ((value == null || value === "") && emptyLabel) {
    return (
      <Field
        label={label}
        value={emptyLabel}
        tone="empty"
        title={title}
      />
    );
  }
  return (
    <Field
      label={label}
      value={fmtSignedBps(value)}
      tone={bpsTone(value ?? null)}
      title={title}
    />
  );
}
