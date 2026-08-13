/**
 * Pure helpers for PnL v2 overview / detail display (WHI-766).
 * Keep render-free so node:test can cover empty / negative / thin-depth states.
 */

import type {
  Direction,
  DriftAnnotation,
  PairOverviewRow,
  PnlBucketTable,
  PnlCostBreakdownUsd,
  PnlOptimalFloorFields,
  PnlOptimalSummary,
  PnlPairSnapshot,
  PnlResult,
  PnlStatus,
} from "./types";
import {
  fmtAgeMs,
  fmtDirection,
  fmtNotional,
  fmtUsd,
  parseNum,
  withdrawalFeeLabel,
  type DirectionVenues,
} from "./format";

export type OverviewPnlCell =
  | {
      kind: "ok";
      pnlUsd: string;
      notionalUsd: string;
      direction: Direction | null;
      title: string;
      /** WHI-821: quiet CEX / aged legs — still show numbers. */
      quoteAged?: boolean;
      ageHint?: string;
      /**
       * WHI-962: green only when min-profit floors + drift bar both clear.
       * False → muted secondary (not capturable under sequential execution).
       */
      capturable?: boolean;
      /** True when net failed k×σ even if USD is positive. */
      failsDrift?: boolean;
    }
  | { kind: "status"; label: string; title: string }
  | { kind: "empty"; label: string; title: string };

const STATUS_LABEL: Record<PnlStatus, string> = {
  ok: "ok",
  no_book: "no book",
  no_pool: "no pool",
  empty_pool: "empty pool",
  invalid_mid: "invalid mid",
  pricing_anomaly: "price anomaly",
  no_depth: "no depth",
  no_fillable: "unfillable",
  fee_unknown: "fee unknown",
  // Legacy wire status (pre-WHI-821 wipe path). Prefer quote_aged annotation.
  stale: "quote aged",
};

export type QuoteAgeFields = {
  quote_aged?: boolean;
  cex_quote_age_ms?: number | null;
  amm_quote_age_ms?: number | null;
  depth_quote_age_ms?: number | null;
};

function agePart(
  label: string,
  ms: number | null | undefined,
): string | null {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return null;
  return `${label} ${fmtAgeMs(ms)} ago`;
}

/** Tooltip / title: "quote aged · CEX 45s ago · AMM 800ms ago". */
export function quoteAgeTitle(ages: QuoteAgeFields): string {
  const parts = [
    agePart("CEX", ages.cex_quote_age_ms),
    agePart("AMM", ages.amm_quote_age_ms),
    agePart("depth", ages.depth_quote_age_ms),
  ].filter((p): p is string => p != null);
  if (parts.length === 0) return "quote aged (quiet book)";
  return `quote aged · ${parts.join(" · ")}`;
}

/** Visible age chip for the dominant (CEX) leg — "aged 45s". */
export function quoteAgedHint(ages: QuoteAgeFields): string | undefined {
  if (!ages.quote_aged) return undefined;
  const cex = ages.cex_quote_age_ms;
  if (cex != null && Number.isFinite(cex) && cex >= 0) {
    return `aged ${fmtAgeMs(cex)}`;
  }
  return "aged";
}

/**
 * Overview Net @ Q* cell helpers (ADR-0002 / WHI-966).
 * Mirrors Bucket PnL quote_aged annotations + blank-status labels so
 * adjacent Net / Bucket PnL columns stay consistent.
 */
export type OverviewNetCell = {
  title: string;
  /** Visible Q* chip when numeric; null when Net is blank. */
  sizeLabel: string | null;
  /**
   * Visible empty-state label when Net is blank (e.g. "no depth") so the
   * cell does not show a bare dash next to Bucket PnL's status text.
   */
  emptyLabel: string | null;
  quoteAged: boolean;
  ageHint?: string;
  /**
   * WHI-962: green only when net clears drift bar **and** min-profit floors.
   * When failsDrift, UI mutes the positive tone.
   */
  capturable?: boolean;
  failsDrift?: boolean;
  driftHint?: string | null;
};

/**
 * WHI-962: profitable highlight = min_profit floors cleared AND not failing
 * the sequential drift bar. Unknown drift (no σ) does not block min_profit.
 */
export function isCapturableOpportunity(row: {
  // One named type shared with the call sites (WHI-973). A bare Pick<> keeps
  // the fields required and does not accept the real overview rows; the body
  // below already treats both as possibly-absent.
  pnl_v2?: PnlOptimalFloorFields | null;
  clears_drift_optimal?: boolean | null;
}): boolean {
  const pnl = row.pnl_v2;
  if (pnl == null || pnl.optimal_net_pnl_usd == null) return false;
  // Prefer explicit meets_min_profit; fall back to positive USD when older API.
  const floors =
    pnl.meets_min_profit !== undefined
      ? Boolean(pnl.meets_min_profit)
      : (parseNum(pnl.optimal_net_pnl_usd) ?? 0) > 0;
  if (!floors) return false;
  // null/undefined σ → do not fail-closed (binance-pancake has no transit σ).
  if (row.clears_drift_optimal === false) return false;
  return true;
}

export function failsDriftBar(row: {
  clears_drift_optimal?: boolean | null;
}): boolean {
  return row.clears_drift_optimal === false;
}

export function driftBarTitle(row: {
  sigma_transit_bps?: string | null;
  drift_premium_bps?: string | null;
  drift_premium_k?: string | null;
  clears_drift_optimal?: boolean | null;
  net_edge_bps?: string | null;
}): string | null {
  if (row.drift_premium_bps == null && row.sigma_transit_bps == null) {
    return null;
  }
  const kPart =
    row.drift_premium_k != null && row.drift_premium_k !== ""
      ? `${row.drift_premium_k}×σ`
      : "k×σ";
  const sigma = row.sigma_transit_bps ?? "—";
  const prem = row.drift_premium_bps ?? "—";
  const net = row.net_edge_bps ?? "—";
  if (row.clears_drift_optimal === false) {
    return `Fails sequential bar: net ${net} bps < ${kPart}=${prem} bps (σ=${sigma})`;
  }
  if (row.clears_drift_optimal === true) {
    return `Clears sequential bar: net ${net} bps ≥ ${kPart}=${prem} bps (σ=${sigma})`;
  }
  return `Sequential bar ${kPart}=${prem} bps (σ=${sigma})`;
}

export function overviewNetCell(
  row: {
    net_edge_bps?: string | null;
    net_size_usd?: string | null;
    amm_quote_reason?: string | null;
    pnl_v2?: (QuoteAgeFields &
      PnlOptimalFloorFields & {
        status?: PnlStatus | null;
      }) | null;
    sigma_transit_bps?: string | null;
    drift_premium_bps?: string | null;
    drift_premium_k?: string | null;
    clears_drift_optimal?: boolean | null;
  },
  emptyTitle?: string | null,
): OverviewNetCell {
  const ages = row.pnl_v2 ?? {};
  const aged = Boolean(ages.quote_aged);
  const agedTitle = aged ? quoteAgeTitle(ages) : null;
  const ageHint = quoteAgedHint(ages);
  const failsDrift = failsDriftBar(row);
  const driftTitle = driftBarTitle(row);
  if (row.net_edge_bps == null) {
    const status = row.pnl_v2?.status ?? null;
    // Same map as overviewPnlCell — exhaustiveness via Record<PnlStatus, …>.
    // status==ok with null bps is the rare unfillable-optimal case (server
    // blanks Net via _has_numeric_optimal); match Bucket PnL's "unfillable".
    const statusLabel =
      status == null
        ? null
        : status === "ok"
          ? STATUS_LABEL.no_fillable
          : STATUS_LABEL[status];
    const base =
      emptyTitle ??
      (statusLabel != null
        ? `No Q* (${statusLabel})`
        : "No Q* yet (needs depth + fillable AMM optimal)");
    return {
      title: agedTitle ? `${base} · ${agedTitle}` : base,
      sizeLabel: null,
      emptyLabel: statusLabel,
      quoteAged: aged,
      ageHint,
      capturable: false,
      failsDrift,
      driftHint: driftTitle,
    };
  }
  const size =
    row.net_size_usd != null ? `Q*=$${fmtNotional(row.net_size_usd)}` : "Q*";
  // bps already a fixed-point wire string; keep as-is for stable titles.
  let title = `Net ${row.net_edge_bps} bps @ ${size} (PnL v2 optimal; same size as Bucket PnL)`;
  if (agedTitle) title = `${title} · ${agedTitle}`;
  if (driftTitle) title = `${title} · ${driftTitle}`;
  const capturable = isCapturableOpportunity(row);
  return {
    title,
    sizeLabel:
      row.net_size_usd != null ? `Q* $${fmtNotional(row.net_size_usd)}` : null,
    emptyLabel: null,
    quoteAged: aged,
    ageHint,
    capturable,
    failsDrift,
    driftHint: driftTitle,
  };
}

export function overviewPnlCell(
  pnl: PnlOptimalSummary | null | undefined,
  venues?: DirectionVenues | null,
  marketId?: string | null,
  drift?: Pick<
    PairOverviewRow,
    | "clears_drift_optimal"
    | "sigma_transit_bps"
    | "drift_premium_bps"
    | "drift_premium_k"
    | "net_edge_bps"
  > | null,
): OverviewPnlCell {
  if (pnl == null) {
    return {
      kind: "empty",
      label: "—",
      title: "PnL v2 not present in API response",
    };
  }
  const agedHint = quoteAgedHint(pnl);
  const agedTitle = pnl.quote_aged ? quoteAgeTitle(pnl) : null;
  // Status first — no_book / no_pool / legacy stale must not collapse into "no depth".
  if (pnl.status !== "ok" && pnl.status !== "no_depth") {
    return {
      kind: "status",
      label: STATUS_LABEL[pnl.status],
      title: agedTitle
        ? `PnL v2 status: ${pnl.status} · ${agedTitle}`
        : `PnL v2 status: ${pnl.status}`,
    };
  }
  if (pnl.status === "no_depth" || !pnl.has_depth) {
    return {
      kind: "status",
      label: agedHint ? `no depth · ${agedHint}` : "no depth",
      title: agedTitle
        ? `No Bybit depth curve in journal — bucket VWAP not available · ${agedTitle}`
        : "No Bybit depth curve in journal — bucket VWAP not available",
    };
  }
  if (pnl.optimal_net_pnl_usd == null) {
    return {
      kind: "status",
      label: STATUS_LABEL.no_fillable,
      title: agedTitle
        ? `PnL v2 status: no_fillable · ${agedTitle}`
        : "PnL v2 status: no_fillable",
    };
  }
  const dir = pnl.direction;
  const notional = pnl.optimal_notional_usd ?? "—";
  let title =
    dir != null
      ? `Optimal ${fmtDirection(dir, venues, marketId)} @ $${fmtNotional(notional)} · ${fmtUsd(pnl.optimal_net_pnl_usd)} USD` +
        (pnl.optimal_net_pnl_bps != null
          ? ` (${Number(pnl.optimal_net_pnl_bps).toFixed(1)} bps)`
          : "")
      : `Optimal PnL ${fmtUsd(pnl.optimal_net_pnl_usd)}`;
  const aged = Boolean(pnl.quote_aged);
  if (aged && agedTitle) {
    title = `${title} · ${agedTitle}`;
  }
  const driftRow = {
    pnl_v2: pnl,
    clears_drift_optimal: drift?.clears_drift_optimal,
    sigma_transit_bps: drift?.sigma_transit_bps,
    drift_premium_bps: drift?.drift_premium_bps,
    drift_premium_k: drift?.drift_premium_k,
    net_edge_bps: drift?.net_edge_bps ?? pnl.optimal_net_pnl_bps,
  };
  const capturable = isCapturableOpportunity(driftRow);
  const failsDrift = failsDriftBar(driftRow);
  const driftTitle = driftBarTitle(driftRow);
  if (driftTitle) title = `${title} · ${driftTitle}`;
  if (!capturable && (failsDrift || pnl.meets_min_profit === false)) {
    title = `${title} · not capturable (floors/drift)`;
  }
  return {
    kind: "ok",
    pnlUsd: pnl.optimal_net_pnl_usd,
    notionalUsd: notional,
    direction: dir,
    title,
    quoteAged: aged,
    ageHint: agedHint,
    capturable,
    failsDrift,
  };
}

/** Format a detail-page drift requirement line (WHI-962). */
export function formatDriftRequirement(
  drift: DriftAnnotation | null | undefined,
  netBps?: string | null,
): string | null {
  if (drift == null || drift.drift_premium_bps == null) return null;
  const k = drift.drift_premium_k;
  const sigma = drift.sigma_transit_bps ?? "—";
  const prem = drift.drift_premium_bps;
  const net = netBps ?? "—";
  const gate =
    drift.clears_drift_optimal === true
      ? "clears"
      : drift.clears_drift_optimal === false
        ? "fails"
        : "n/a";
  return `Sequential bar: net ${net} bps ${gate === "clears" ? "≥" : gate === "fails" ? "<" : "vs"} ${k}×σ=${prem} bps (σ=${sigma} · ${drift.session ?? "session?"}) · ${gate}`;
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
  { key: "withdrawal_fee_usd", label: "Withdrawal" },
];

export function usdCostRows(
  costs: PnlCostBreakdownUsd | null | undefined,
): Array<{ key: string; label: string; value: number }> {
  if (!costs) return [];
  return USD_COST_ROWS.map((r) => {
    const label =
      r.key === "withdrawal_fee_usd"
        ? withdrawalFeeLabel(costs.withdrawal_fee_kind)
        : r.label;
    return {
      key: r.key,
      label,
      value: parseNum(costs[r.key] as string) ?? 0,
    };
  });
}

export function totalCostUsd(
  costs: PnlCostBreakdownUsd | null | undefined,
): number | null {
  const rows = usdCostRows(costs);
  if (rows.length === 0) return null;
  return rows.reduce((a, r) => a + r.value, 0);
}

export { STATUS_LABEL };
