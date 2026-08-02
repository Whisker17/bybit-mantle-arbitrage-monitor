/**
 * Pure helpers for PnL v2 overview / detail display (WHI-766).
 * Keep render-free so node:test can cover empty / negative / thin-depth states.
 */

import type {
  Direction,
  PnlBucketTable,
  PnlCostBreakdownUsd,
  PnlOptimalSummary,
  PnlPairSnapshot,
  PnlResult,
  PnlStatus,
} from "./types";
import {
  fmtDirection,
  fmtNotional,
  fmtUsd,
  parseNum,
  type DirectionVenues,
} from "./format";

export type OverviewPnlCell =
  | {
      kind: "ok";
      pnlUsd: string;
      notionalUsd: string;
      direction: Direction | null;
      title: string;
    }
  | { kind: "status"; label: string; title: string }
  | { kind: "empty"; label: string; title: string };

const STATUS_LABEL: Record<PnlStatus, string> = {
  ok: "ok",
  no_book: "no book",
  no_pool: "no pool",
  no_depth: "no depth",
  no_fillable: "unfillable",
  stale: "stale",
};

export function overviewPnlCell(
  pnl: PnlOptimalSummary | null | undefined,
  venues?: DirectionVenues | null,
  marketId?: string | null,
): OverviewPnlCell {
  if (pnl == null) {
    return {
      kind: "empty",
      label: "—",
      title: "PnL v2 not present in API response",
    };
  }
  // Status first — no_book / no_pool / stale must not collapse into "no depth".
  if (pnl.status !== "ok" && pnl.status !== "no_depth") {
    return {
      kind: "status",
      label: STATUS_LABEL[pnl.status],
      title: `PnL v2 status: ${pnl.status}`,
    };
  }
  if (pnl.status === "no_depth" || !pnl.has_depth) {
    return {
      kind: "status",
      label: "no depth",
      title: "No Bybit depth curve in journal — bucket VWAP not available",
    };
  }
  if (pnl.optimal_net_pnl_usd == null) {
    return {
      kind: "status",
      label: STATUS_LABEL.no_fillable,
      title: "PnL v2 status: no_fillable",
    };
  }
  const dir = pnl.direction;
  const notional = pnl.optimal_notional_usd ?? "—";
  const title =
    dir != null
      ? `Optimal ${fmtDirection(dir, venues, marketId)} @ $${fmtNotional(notional)} · ${fmtUsd(pnl.optimal_net_pnl_usd)} USD` +
        (pnl.optimal_net_pnl_bps != null
          ? ` (${Number(pnl.optimal_net_pnl_bps).toFixed(1)} bps)`
          : "")
      : `Optimal PnL ${fmtUsd(pnl.optimal_net_pnl_usd)}`;
  return {
    kind: "ok",
    pnlUsd: pnl.optimal_net_pnl_usd,
    notionalUsd: notional,
    direction: dir,
    title,
  };
}

export function pickBucketTable(
  snap: PnlPairSnapshot | null | undefined,
  direction: Direction,
): PnlBucketTable | null {
  if (!snap?.tables) return null;
  return snap.tables[direction] ?? null;
}

/** True when depth exists but large buckets are unfillable (thin book). */
export function isThinDepth(table: PnlBucketTable | null): boolean {
  if (!table) return false;
  const buckets = table.amm_buckets;
  if (buckets.length === 0) return false;
  const anyFillable = buckets.some((b) => b.fillable);
  const anyUnfillable = buckets.some((b) => !b.fillable);
  return anyFillable && anyUnfillable;
}

export function isOptimalBucket(
  row: PnlResult,
  table: PnlBucketTable | null,
): boolean {
  if (!table?.optimal) return false;
  const q = parseNum(table.optimal.q_star_usd);
  const size = parseNum(row.size_usd);
  if (q == null || size == null) return false;
  // Highlight the fixed bucket closest to Q* (log-grid optima land between rungs).
  let nearest: number | null = null;
  let nearestDist = Infinity;
  for (const b of table.amm_buckets) {
    if (!b.fillable) continue;
    const s = parseNum(b.size_usd);
    if (s == null) continue;
    const d = Math.abs(s - q);
    if (d < nearestDist) {
      nearestDist = d;
      nearest = s;
    }
  }
  return nearest != null && size === nearest;
}

const USD_COST_ROWS: Array<{ key: keyof PnlCostBreakdownUsd; label: string }> = [
  { key: "bybit_fee_usd", label: "Bybit fee" },
  { key: "fluxion_fee_usd", label: "Fluxion fee" },
  { key: "bybit_slip_usd", label: "Bybit slip" },
  { key: "fluxion_slip_usd", label: "Fluxion slip" },
  { key: "gas_usd", label: "Gas" },
  { key: "basis_usd", label: "USDT/USDC basis" },
];

export function usdCostRows(
  costs: PnlCostBreakdownUsd | null | undefined,
): Array<{ key: string; label: string; value: number }> {
  if (!costs) return [];
  return USD_COST_ROWS.map((r) => ({
    key: r.key,
    label: r.label,
    value: parseNum(costs[r.key]) ?? 0,
  }));
}

export function totalCostUsd(
  costs: PnlCostBreakdownUsd | null | undefined,
): number | null {
  const rows = usdCostRows(costs);
  if (rows.length === 0) return null;
  return rows.reduce((a, r) => a + r.value, 0);
}

export { STATUS_LABEL };
