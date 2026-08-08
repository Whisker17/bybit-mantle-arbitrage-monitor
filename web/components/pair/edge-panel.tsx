"use client";

import { useEffect, useMemo, useState } from "react";

import {
  bpsTone,
  directionToggleLabel,
  fmtBps,
  fmtDirection,
  fmtDirectionTitle,
  fmtNotional,
  fmtSignedBps,
  fmtUsd,
  parseNum,
  resolveVenues,
  totalWearBps,
  usdTone,
  type DirectionVenues,
} from "@/lib/format";
import {
  isOptimalBucket,
  isThinDepth,
  pickBucketTable,
  quoteAgeTitle,
  quoteAgedHint,
  totalCostUsd,
  usdCostRows,
} from "@/lib/pnl";
import type {
  CostBreakdown,
  Direction,
  Distribution,
  EdgePanel,
  PnlBucketTable,
  PnlPairSnapshot,
  PnlResult,
} from "@/lib/types";
import { cn } from "@/lib/cn";

type Props = {
  amm: EdgePanel;
  rfq: EdgePanel;
  pnl?: PnlPairSnapshot | null;
  /** Hide RFQ venue card when market has no RFQ (WHI-774). */
  hasRfq?: boolean;
  /** Explicit venues preferred over marketId split (WHI-780). */
  venues?: DirectionVenues | null;
  marketId?: string;
};

const DIRECTION_IDS: Direction[] = [
  "buy_fluxion_sell_bybit",
  "buy_bybit_sell_fluxion",
];

export function EdgeStatsPanel({
  amm,
  rfq,
  pnl,
  hasRfq = true,
  venues: venuesProp,
  marketId,
}: Props) {
  const venues = resolveVenues(venuesProp, marketId);
  const directions = DIRECTION_IDS.map((id) => ({
    id,
    label: directionToggleLabel(id, venues, marketId),
  }));
  const [direction, setDirection] = useState<Direction>(
    "buy_fluxion_sell_bybit",
  );

  // Prefer best optimal direction once the API payload arrives / updates.
  useEffect(() => {
    if (pnl?.best.direction) {
      setDirection(pnl.best.direction);
    }
  }, [pnl?.best.direction]);

  const table = useMemo(
    () => pickBucketTable(pnl, direction),
    [pnl, direction],
  );

  return (
    <div className="space-y-3">
      <p className="text-[10px] text-muted-foreground">
        Net paper edge (M3) — cumulative over recent journal book samples
        (each distribution row prints its own n=). PnL v2 below is cash-flow
        at fixed USD buckets + sample-best optimal size.
      </p>
      <div
        className={cn(
          "grid gap-3",
          hasRfq ? "lg:grid-cols-2" : "lg:grid-cols-1",
        )}
      >
        <VenueEdgeCard title="AMM" panel={amm} venues={venues} marketId={marketId} />
        {hasRfq && (
          <VenueEdgeCard
            title="RFQ"
            panel={rfq}
            note="unslipped ladder (see DEFERRED)"
            venues={venues}
            marketId={marketId}
          />
        )}
      </div>
      <BucketPnlPanel
        snap={pnl}
        direction={direction}
        onDirection={setDirection}
        table={table}
        directions={directions}
        venues={venues}
        marketId={marketId}
      />
    </div>
  );
}

function VenueEdgeCard({
  title,
  panel,
  note,
  venues,
  marketId,
}: {
  title: string;
  panel: EdgePanel;
  note?: string;
  venues?: DirectionVenues | null;
  marketId?: string;
}) {
  const cur = panel.current;
  const costs = cur?.costs ?? panel.costs;

  return (
    <div className="rounded-md border border-border bg-card px-3 py-2.5 space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold">{title}</h3>
        {note && (
          <span className="text-[10px] text-muted-foreground">{note}</span>
        )}
      </div>

      {cur == null ? (
        <p className="text-[11px] text-muted-foreground">No fillable edge</p>
      ) : (
        <div className="space-y-1 text-xs">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <Bps value={cur.net_edge_bps} className="text-sm font-semibold" />
            <span
              className="text-muted-foreground"
              title={fmtDirectionTitle(cur.direction, venues, marketId)}
            >
              {fmtDirection(cur.direction, venues, marketId)} @ $
              {Number(cur.size_usd).toLocaleString()}
            </span>
            <span className="text-muted-foreground">
              gross {fmtSignedBps(cur.gross_spread_bps)}
            </span>
          </div>
        </div>
      )}

      <CostWaterfall costs={costs} />

      <div className="space-y-0.5 border-t border-border/60 pt-2 text-[11px]">
        <DistRow label="all" dist={panel.distribution_all} />
        <DistRow label="open" dist={panel.distribution_open} />
        <DistRow label="closed" dist={panel.distribution_closed} />
        <p className="text-muted-foreground pt-0.5">
          breaches: episodes={panel.breach_all.episode_count}{" "}
          dur_ms={panel.breach_all.total_duration_ms}{" "}
          now={panel.breach_all.currently_breaching ? "Y" : "N"}
        </p>
      </div>
    </div>
  );
}

function DistRow({ label, dist }: { label: string; dist: Distribution }) {
  if (dist.count === 0) {
    return (
      <p className="tabular-nums text-muted-foreground">
        {label}: n=0
      </p>
    );
  }
  return (
    <p className="tabular-nums">
      <span className="text-muted-foreground">{label}:</span> n={dist.count}{" "}
      p50={fmtBps(dist.p50)} p95={fmtBps(dist.p95)} p99={fmtBps(dist.p99)}{" "}
      max={fmtBps(dist.max)}
    </p>
  );
}

const WEAR_ROWS: Array<{
  key: keyof CostBreakdown;
  label: string;
}> = [
  { key: "bybit_taker_bps", label: "Bybit fee" },
  { key: "fluxion_fee_bps", label: "Fluxion fee" },
  { key: "bybit_slip_bps", label: "Bybit slip" },
  { key: "fluxion_slip_bps", label: "Fluxion slip" },
  { key: "gas_bps", label: "Gas" },
  { key: "basis_bps", label: "USDT/USDC basis" },
  { key: "withdrawal_fee_bps", label: "Withdrawal" },
];

function wearLabel(key: keyof CostBreakdown, costs: CostBreakdown): string {
  if (key !== "withdrawal_fee_bps") {
    return WEAR_ROWS.find((r) => r.key === key)?.label ?? String(key);
  }
  if (costs.withdrawal_fee_kind === "unknown") return "Withdrawal (unknown)";
  if (costs.withdrawal_fee_kind === "stable") return "Withdrawal (stable)";
  if (costs.withdrawal_fee_kind === "asset") return "Withdrawal (asset)";
  return "Withdrawal";
}

function CostWaterfall({ costs }: { costs: CostBreakdown | null }) {
  if (!costs) {
    return (
      <p className="text-[10px] text-muted-foreground">No cost breakdown</p>
    );
  }
  const total = totalWearBps(costs);
  const totalN = parseNum(total) ?? 0;
  const maxBar = Math.max(
    totalN,
    ...WEAR_ROWS.map((r) => Math.abs(parseNum(costs[r.key] as string) ?? 0)),
    1,
  );

  return (
    <div className="space-y-1">
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        Cost waterfall (bps)
      </p>
      {WEAR_ROWS.map((r) => {
        const v = parseNum(costs[r.key] as string) ?? 0;
        const pct = Math.min(100, (Math.abs(v) / maxBar) * 100);
        return (
          <div key={r.key} className="grid grid-cols-[7.5rem_1fr_2.5rem] items-center gap-1.5 text-[10px]">
            <span className="text-muted-foreground truncate">
              {wearLabel(r.key, costs)}
            </span>
            <div className="h-1.5 rounded-sm bg-muted overflow-hidden">
              <div
                className="h-full rounded-sm bg-warning/70"
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="tabular-nums text-right">{fmtBps(v)}</span>
          </div>
        );
      })}
      <div className="grid grid-cols-[7.5rem_1fr_2.5rem] items-center gap-1.5 text-[10px] font-medium">
        <span>Total wear</span>
        <div />
        <span className="tabular-nums text-right">{fmtBps(total)}</span>
      </div>
    </div>
  );
}

function Bps({
  value,
  className,
}: {
  value: string | null;
  className?: string;
}) {
  const tone = bpsTone(value);
  return (
    <span
      className={cn(
        "tabular-nums",
        tone === "pos" && "text-positive",
        tone === "neg" && "text-negative",
        tone === "empty" && "text-muted-foreground",
        className,
      )}
    >
      {fmtSignedBps(value)} bps
    </span>
  );
}

function BucketPnlPanel({
  snap,
  direction,
  onDirection,
  table,
  directions,
  venues,
  marketId,
}: {
  snap: PnlPairSnapshot | null | undefined;
  direction: Direction;
  onDirection: (d: Direction) => void;
  table: PnlBucketTable | null;
  directions: Array<{ id: Direction; label: string }>;
  venues?: DirectionVenues | null;
  marketId?: string;
}) {
  if (snap == null) {
    return (
      <div className="rounded-md border border-dashed border-border px-3 py-2.5">
        <p className="text-xs font-semibold mb-1">Bucket PnL / optimal size</p>
        <p className="text-[11px] text-muted-foreground">
          PnL v2 payload missing from API response.
        </p>
      </div>
    );
  }

  const thin = isThinDepth(table);
  const best = snap.best;

  return (
    <div className="rounded-md border border-border bg-card px-3 py-2.5 space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-xs font-semibold">Bucket PnL / optimal size</h3>
          <p className="text-[10px] text-muted-foreground">
            Cash-flow paper arb · $10–$10K AMM buckets
            {!snap.has_depth && " · L1 (no depth curve)"}
            {thin && " · thin depth (large sizes unfillable)"}
            {(snap.quote_aged || best.quote_aged) &&
              " · quote aged (quiet book — still computed)"}
            {best.status === "pricing_anomaly" &&
              " · price anomaly (not a tradable claim)"}
          </p>
        </div>
        <div className="flex gap-1">
          {directions.map((d) => (
            <button
              key={d.id}
              type="button"
              onClick={() => onDirection(d.id)}
              className={cn(
                "rounded px-2 py-0.5 text-[10px] border transition-colors",
                direction === d.id
                  ? "border-foreground/40 bg-muted text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {d.label}
            </button>
          ))}
        </div>
      </div>

      {best.status === "pricing_anomaly" && (
        <p
          className="text-[11px] text-warning"
          title="|AMM vs CEX| exceeds max_abs_amm_spread_bps — mid kept for investigation; PnL optimal suppressed (WHI-822)"
        >
          Price anomaly — extreme |AMM vs CEX|; not treated as a fillable
          paper opportunity. Bucket rows below are diagnostic only.
        </p>
      )}

      {best.status === "ok" && best.optimal_net_pnl_usd != null && (
        <p className="text-xs">
          <span className="text-muted-foreground">Best optimal: </span>
          <span
            className={cn(
              "tabular-nums font-semibold",
              usdTone(best.optimal_net_pnl_usd) === "pos" && "text-positive",
              usdTone(best.optimal_net_pnl_usd) === "neg" && "text-negative",
            )}
          >
            {fmtUsd(best.optimal_net_pnl_usd)}
          </span>
          <span
            className="text-muted-foreground"
            title={fmtDirectionTitle(best.direction, venues, marketId)}
          >
            {" "}
            @ ${fmtNotional(best.optimal_notional_usd)}{" "}
            {fmtDirection(best.direction, venues, marketId)}
          </span>
          {(snap.quote_aged || best.quote_aged) && (
            <span
              className="ml-2 text-[10px] text-warning"
              title={quoteAgeTitle(snap)}
            >
              {quoteAgedHint(snap) ?? "quote aged"}
            </span>
          )}
        </p>
      )}

      {!table ? (
        <p className="text-[11px] text-muted-foreground">
          No bucket table for this direction ({snap.status}).
        </p>
      ) : (
        <>
          <BucketTable table={table} />
          {(() => {
            const costRow =
              table.optimal?.result ??
              [...table.amm_buckets].reverse().find((b) => b.fillable) ??
              null;
            if (!costRow) return null;
            const title = table.optimal
              ? `Costs at optimal Q*=$${fmtNotional(table.optimal.q_star_usd)}`
              : `Costs at Q=$${fmtNotional(costRow.size_usd)}`;
            return <UsdCostWaterfall title={title} row={costRow} />;
          })()}
          {table.rfq_rows.length > 0 && (
            <div className="space-y-1 pt-1 border-t border-border/60">
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                RFQ poll reference
              </p>
              {table.rfq_rows.map((r) => (
                <p key={`${r.venue}-${r.size_usd}`} className="text-[11px] tabular-nums">
                  Q=${fmtNotional(r.size_usd)} · PnL {fmtUsd(r.pnl_usd)}
                  {!r.fillable && r.reason ? ` · ${r.reason}` : ""}
                </p>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function BucketTable({ table }: { table: PnlBucketTable }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px] border-collapse">
        <thead>
          <tr className="text-muted-foreground border-b border-border/60">
            <th className="text-left font-medium py-1 pr-2">Size</th>
            <th className="text-right font-medium py-1 px-2">PnL USD</th>
            <th className="text-right font-medium py-1 px-2">bps</th>
            <th className="text-right font-medium py-1 px-2">Bybit fee</th>
            <th className="text-right font-medium py-1 px-2">Flux fee</th>
            <th className="text-right font-medium py-1 px-2">Gas</th>
            <th className="text-right font-medium py-1 pl-2">Slip</th>
          </tr>
        </thead>
        <tbody>
          {table.amm_buckets.map((row) => {
            const opt = isOptimalBucket(row, table);
            const tone = usdTone(row.pnl_usd);
            return (
              <tr
                key={row.size_usd}
                className={cn(
                  "border-b border-border/40",
                  opt && "bg-muted/50",
                  !row.fillable && "opacity-50",
                )}
              >
                <td className="py-1 pr-2 tabular-nums">
                  ${fmtNotional(row.size_usd)}
                  {opt && (
                    <span className="ml-1 text-[9px] text-muted-foreground">
                      ≈Q*
                    </span>
                  )}
                </td>
                <td
                  className={cn(
                    "py-1 px-2 text-right tabular-nums font-medium",
                    tone === "pos" && "text-positive",
                    tone === "neg" && "text-negative",
                  )}
                >
                  {row.fillable ? fmtUsd(row.pnl_usd) : row.reason ?? "—"}
                </td>
                <td className="py-1 px-2 text-right tabular-nums text-muted-foreground">
                  {row.fillable ? fmtSignedBps(row.pnl_bps) : "—"}
                </td>
                <td className="py-1 px-2 text-right tabular-nums">
                  {fmtUsd(row.costs.bybit_fee_usd)}
                </td>
                <td className="py-1 px-2 text-right tabular-nums">
                  {fmtUsd(row.costs.fluxion_fee_usd)}
                </td>
                <td className="py-1 px-2 text-right tabular-nums">
                  {fmtUsd(row.costs.gas_usd)}
                </td>
                <td className="py-1 pl-2 text-right tabular-nums">
                  {fmtUsd(
                    (
                      (parseNum(row.costs.bybit_slip_usd) ?? 0) +
                      (parseNum(row.costs.fluxion_slip_usd) ?? 0)
                    ).toString(),
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function UsdCostWaterfall({
  title,
  row,
}: {
  title: string;
  row: PnlResult;
}) {
  const costs = usdCostRows(row.costs);
  const total = totalCostUsd(row.costs) ?? 0;
  const maxBar = Math.max(total, ...costs.map((c) => Math.abs(c.value)), 1e-9);

  return (
    <div className="space-y-1 border-t border-border/60 pt-2">
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      {costs.map((c) => {
        const pct = Math.min(100, (Math.abs(c.value) / maxBar) * 100);
        return (
          <div
            key={c.key}
            className="grid grid-cols-[7.5rem_1fr_3rem] items-center gap-1.5 text-[10px]"
          >
            <span className="text-muted-foreground truncate">{c.label}</span>
            <div className="h-1.5 rounded-sm bg-muted overflow-hidden">
              <div
                className="h-full rounded-sm bg-warning/70"
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="tabular-nums text-right">{fmtUsd(c.value)}</span>
          </div>
        );
      })}
      <div className="grid grid-cols-[7.5rem_1fr_3rem] items-center gap-1.5 text-[10px] font-medium">
        <span>Total costs</span>
        <div />
        <span className="tabular-nums text-right">{fmtUsd(total)}</span>
      </div>
    </div>
  );
}
