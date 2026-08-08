import type {
  AmmQuoteReason,
  DexNonTradeableReason,
  Direction,
  SessionKind,
} from "./types";

const DASH = "—";

/** Human label for AMM quote suppression / anomaly (WHI-795 / WHI-822). */
const AMM_QUOTE_REASON_LABEL: Record<AmmQuoteReason, string> = {
  empty_pool: "empty pool",
  invalid_mid: "invalid mid",
  pricing_anomaly: "price anomaly",
};

/** Pair-id badge labels for non-tradeable DEX legs (WHI-796 / WHI-822). */
const DEX_NON_TRADEABLE_LABEL: Record<DexNonTradeableReason, string> = {
  empty_pool: AMM_QUOTE_REASON_LABEL.empty_pool,
  invalid_mid: AMM_QUOTE_REASON_LABEL.invalid_mid,
  pricing_anomaly: AMM_QUOTE_REASON_LABEL.pricing_anomaly,
  no_pool: "no pool",
  low_liq: "low liq",
  no_quote: "no quote",
};

const DEX_NON_TRADEABLE_TITLE: Record<DexNonTradeableReason, string> = {
  empty_pool: "Pool has zero in-range liquidity — residual slot0 mid suppressed",
  invalid_mid: "AMM mid non-positive — suppressed",
  pricing_anomaly:
    "|AMM vs CEX| exceeds max_abs_amm_spread_bps — mid shown for investigation, not a tradable claim (WHI-822)",
  no_pool:
    "No AMM pool in inventory (dex:none) — CEX-only; excluded from Top-N unless RFQ is two-sided",
  low_liq: "Below low_liquidity_threshold_usd — excluded from Top-N seats",
  no_quote: "Waiting for a quotable AMM mid or two-sided RFQ — no Top-N seat yet",
};

/** Badge text for a {@link DexNonTradeableReason}. */
export function dexNonTradeableLabel(
  reason: DexNonTradeableReason | null | undefined,
): string | null {
  if (reason == null) return null;
  return DEX_NON_TRADEABLE_LABEL[reason];
}

/** Hover copy for a non-tradeable reason badge. */
export function dexNonTradeableTitle(
  reason: DexNonTradeableReason | null | undefined,
): string | undefined {
  if (reason == null) return undefined;
  return DEX_NON_TRADEABLE_TITLE[reason];
}

/**
 * Label for AMM mid / vs CEX / AMM vs Und when the mid is suppressed.
 * Returns null when the reason is absent (plain dash / n/a is enough).
 */
export function ammQuoteReasonLabel(
  reason: AmmQuoteReason | null | undefined,
): string | null {
  if (reason == null) return null;
  return AMM_QUOTE_REASON_LABEL[reason];
}

/** Prefer formatted value; when null and reason present, show reason label. */
export function fmtOrAmmReason(
  formatted: string,
  value: string | number | null | undefined,
  reason: AmmQuoteReason | null | undefined,
): string {
  if (value !== null && value !== undefined && value !== "") return formatted;
  return ammQuoteReasonLabel(reason) ?? formatted;
}

/** Shared hover copy when AMM mid is suppressed or anomalous (WHI-795 / WHI-822). */
export function ammQuoteReasonTitle(
  reason: AmmQuoteReason | null | undefined,
): string | undefined {
  if (reason == null) return undefined;
  // AmmQuoteReason ⊆ DexNonTradeableReason for title copy.
  return DEX_NON_TRADEABLE_TITLE[reason];
}

/**
 * CEX mid vs underlying (bps). Prefers explicit cex field; falls back to
 * legacy ``premium_bps`` alias from WHI-779 (WHI-783 overview CEX vs Und).
 */
export function cexPremiumBps(
  row: {
    cex_premium_bps?: string | null;
    premium_bps?: string | null;
  } | null | undefined,
): string | null {
  if (!row) return null;
  return row.cex_premium_bps ?? row.premium_bps ?? null;
}

/** Badge variant for underlying price_type (WHI-779). */
export function priceTypeBadgeVariant(
  pt: string | null | undefined,
): "open" | "closed" | "warning" | "muted" {
  if (pt === "live") return "open";
  if (pt === "stale") return "warning";
  if (pt === "pre" || pt === "post" || pt === "close") return "closed";
  return "muted";
}

/**
 * Display label for underlying price_type (WHI-821 disambiguation).
 * Raw price type is authoritative for the Underlying *reference* column
 * (WHI-783: not a premium phrase). Only remap ``stale`` → "price stale"
 * so it never collides with row "no book" or PnL "quote aged". Prefer
 * ``typeLabel`` only when priceType is absent (API partial).
 */
export function fmtUnderlyingPriceType(
  priceType: string | null | undefined,
  typeLabel?: string | null,
): string | null {
  if (priceType === "stale") return "price stale";
  if (priceType != null && priceType !== "") return priceType;
  if (typeLabel) return typeLabel;
  return null;
}

/**
 * True when an underlying print is usable for display / vs-Und (WHI-794).
 * Rejects null, non-finite, ≤0, and epoch-zero as_of.
 */
export function isRealUnderlyingPrint(
  price: string | number | null | undefined,
  asOfMs?: number | null,
): boolean {
  if (price === null || price === undefined || price === "") return false;
  const n = typeof price === "number" ? price : Number(price);
  if (!Number.isFinite(n) || n <= 0) return false;
  if (asOfMs != null && asOfMs <= 0) return false;
  return true;
}

export function fmtPrice(
  value: string | number | null | undefined,
  digits = 4,
): string {
  if (value === null || value === undefined || value === "") return DASH;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  // WHI-794: 0 is not a price (never-published Pyth / empty book).
  // Matches acceptance "no 0.0000 as price anywhere on the page".
  if (n <= 0) return DASH;
  return n.toFixed(digits);
}

export function fmtBps(
  value: string | number | null | undefined,
  digits = 1,
): string {
  if (value === null || value === undefined || value === "") return DASH;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  return n.toFixed(digits);
}

export function fmtSignedBps(
  value: string | number | null | undefined,
  digits = 1,
): string {
  if (value === null || value === undefined || value === "") return DASH;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)}`;
}

export function fmtNotional(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return DASH;
  const v = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(v)) return String(value);
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return v.toFixed(0);
}

/** CEX/DEX volume ratio (compact ×). */
export function fmtVolumeRatio(
  value: string | number | null | undefined,
  digits = 1,
): string {
  if (value === null || value === undefined || value === "") return DASH;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K×`;
  if (n >= 100) return `${n.toFixed(0)}×`;
  return `${n.toFixed(digits)}×`;
}

/** UTC HH:MM for truncated DEX volume labels (WHI-777). */
export function fmtUtcHm(tsMs: number | null | undefined): string {
  if (tsMs === null || tsMs === undefined || !Number.isFinite(tsMs)) return DASH;
  const d = new Date(tsMs);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

/** Signed USD (PnL v2). Compact for overview cells. */
export function fmtUsd(
  value: string | number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || value === "") return DASH;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  const sign = n > 0 ? "+" : "";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${sign}${(n / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}${(n / 1_000).toFixed(2)}K`;
  return `${sign}${n.toFixed(digits)}`;
}

/** Same positive/negative tone as bps, for USD PnL. */
export function usdTone(
  value: string | number | null | undefined,
): "pos" | "neg" | "flat" | "empty" {
  return bpsTone(value);
}

/**
 * Venue pair for market-aware direction labels (WHI-780).
 * Wire enums stay historical (`buy_fluxion_sell_bybit` = buy DEX sell CEX).
 */
export type DirectionVenues = {
  /** CEX venue id, e.g. ``bybit`` / ``binance``. */
  cex: string;
  /** DEX venue id, e.g. ``fluxion`` / ``pancake``. */
  dex: string;
};

const DEFAULT_VENUES: DirectionVenues = { cex: "bybit", dex: "fluxion" };

/** Short single-letter codes for dense Dir cells. */
const VENUE_CODE: Record<string, string> = {
  bybit: "B",
  binance: "B",
  fluxion: "F",
  pancake: "P",
};

/** Human venue names for group headers / tooltips. */
const VENUE_LABEL: Record<string, string> = {
  bybit: "Bybit",
  binance: "Binance",
  fluxion: "Fluxion",
  pancake: "Pancake",
};

export function venueCode(venue: string): string {
  const key = venue.trim().toLowerCase();
  if (VENUE_CODE[key]) return VENUE_CODE[key];
  const c = key.charAt(0).toUpperCase();
  return c || "?";
}

export function venueLabel(venue: string): string {
  const key = venue.trim().toLowerCase();
  if (VENUE_LABEL[key]) return VENUE_LABEL[key];
  if (!key) return venue;
  return key.charAt(0).toUpperCase() + key.slice(1);
}

/**
 * Derive venues from a market id (`bybit-fluxion` → bybit/fluxion).
 * Prefer explicit API / market-card ``cex_venue``/``dex_venue`` via
 * {@link resolveVenues} — id-split is only a fallback for known id shapes.
 */
export function venuesFromMarketId(
  marketId: string | null | undefined,
): DirectionVenues {
  if (!marketId) return { ...DEFAULT_VENUES };
  const parts = marketId.split("-").filter(Boolean);
  if (parts.length >= 2) {
    return { cex: parts[0]!.toLowerCase(), dex: parts[1]!.toLowerCase() };
  }
  return { ...DEFAULT_VENUES };
}

/** Prefer explicit venues; fall back to market-id split; else Bybit/Fluxion. */
export function resolveVenues(
  venues?: DirectionVenues | null,
  marketId?: string | null,
): DirectionVenues {
  if (venues?.cex && venues?.dex) {
    return { cex: venues.cex.toLowerCase(), dex: venues.dex.toLowerCase() };
  }
  return venuesFromMarketId(marketId);
}

/**
 * Short direction label: buy DEX→CEX or CEX→DEX using venue codes.
 * Defaults to Bybit⇄Fluxion (``F→B`` / ``B→F``) when venues omitted.
 */
export function fmtDirection(
  direction: Direction | null | undefined,
  venues?: DirectionVenues | null,
  marketId?: string | null,
): string {
  if (!direction) return DASH;
  const v = resolveVenues(venues, marketId);
  const cex = venueCode(v.cex);
  const dex = venueCode(v.dex);
  // Wire: buy_fluxion_sell_bybit = buy DEX, sell CEX.
  if (direction === "buy_fluxion_sell_bybit") return `${dex}→${cex}`;
  return `${cex}→${dex}`;
}

/** Full-words tooltip for Dir cells (no wire-enum leakage — WHI-780). */
export function fmtDirectionTitle(
  direction: Direction | null | undefined,
  venues?: DirectionVenues | null,
  marketId?: string | null,
): string | undefined {
  if (!direction) return undefined;
  const v = resolveVenues(venues, marketId);
  const cex = venueLabel(v.cex);
  const dex = venueLabel(v.dex);
  if (direction === "buy_fluxion_sell_bybit") {
    return `Buy ${dex}, sell ${cex}`;
  }
  return `Buy ${cex}, sell ${dex}`;
}

/** Direction toggle labels for pair-detail edge panel. */
export function directionToggleLabel(
  direction: Direction,
  venues?: DirectionVenues | null,
  marketId?: string | null,
): string {
  const short = fmtDirection(direction, venues, marketId);
  const v = resolveVenues(venues, marketId);
  if (direction === "buy_fluxion_sell_bybit") {
    return `${short} (buy ${venueLabel(v.dex)})`;
  }
  return `${short} (buy ${venueLabel(v.cex)})`;
}

export function fmtSession(session: SessionKind | null | undefined): string {
  if (!session) return "?";
  return session === "open" ? "OPEN" : "CLOSED";
}

/** Human age like "12s", "3m", "1h" from milliseconds. */
export function fmtAgeMs(ageMs: number | null | undefined): string {
  if (ageMs === null || ageMs === undefined) return DASH;
  if (ageMs < 1000) return `${ageMs}ms`;
  if (ageMs < 60_000) return `${Math.round(ageMs / 1000)}s`;
  if (ageMs < 3_600_000) return `${Math.round(ageMs / 60_000)}m`;
  return `${(ageMs / 3_600_000).toFixed(1)}h`;
}

export function fmtTsMs(tsMs: number | null | undefined): string {
  if (tsMs === null || tsMs === undefined) return DASH;
  try {
    return new Date(tsMs).toLocaleTimeString(undefined, {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return String(tsMs);
  }
}

export function parseNum(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Classify bps for color: positive / negative / flat. */
export function bpsTone(
  value: string | number | null | undefined,
): "pos" | "neg" | "flat" | "empty" {
  const n = parseNum(value);
  if (n === null) return "empty";
  if (n > 0) return "pos";
  if (n < 0) return "neg";
  return "flat";
}

/** Shorten 0x… addresses / hashes for dense tables (matches TUI short_addr). */
export function shortAddr(
  addr: string | null | undefined,
  head = 6,
  tail = 4,
): string {
  if (!addr) return "—";
  // Strip optional 0x for length math, keep prefix in display.
  const has0x = addr.startsWith("0x") || addr.startsWith("0X");
  const body = has0x ? addr.slice(2) : addr;
  if (body.length <= head + tail) return addr;
  const prefix = has0x ? "0x" : "";
  return `${prefix}${body.slice(0, head)}…${body.slice(-tail)}`;
}

/**
 * Mantle mainnet explorer base for tx links.
 * Override via NEXT_PUBLIC_EXPLORER_TX_BASE (no trailing slash), same pattern
 * as NEXT_PUBLIC_API_BASE — not a secret, but not baked into every call site.
 */
export function explorerTxBase(): string {
  if (typeof process !== "undefined" && process.env.NEXT_PUBLIC_EXPLORER_TX_BASE) {
    return process.env.NEXT_PUBLIC_EXPLORER_TX_BASE.replace(/\/$/, "");
  }
  return "https://mantlescan.xyz";
}

/** Mantle mainnet explorer link for a transaction hash. */
export function explorerTxUrl(txHash: string | null | undefined): string | null {
  if (!txHash) return null;
  const h = txHash.startsWith("0x") ? txHash : `0x${txHash}`;
  return `${explorerTxBase()}/tx/${h}`;
}

/** Fraction 0..1 → "12.3%"; null → em dash. */
export function fmtPct(
  value: number | null | undefined,
  digits = 1,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return `${(value * 100).toFixed(digits)}%`;
}

export function fmtLabel(label: string | null | undefined): string {
  if (!label) return "—";
  return label.replaceAll("_", " ");
}

/** WHI-961: shared labels for withdrawal_fee_kind on cost waterfalls. */
export const WITHDRAWAL_FEE_KIND_LABEL: Record<
  "stable" | "asset" | "unknown",
  string
> = {
  stable: "Withdrawal (stable)",
  asset: "Withdrawal (asset)",
  unknown: "Withdrawal (unknown)",
};

export function withdrawalFeeLabel(
  kind: "stable" | "asset" | "unknown" | null | undefined,
): string {
  if (kind == null) return "Withdrawal";
  return WITHDRAWAL_FEE_KIND_LABEL[kind] ?? "Withdrawal";
}

/**
 * Sum of CostBreakdown wear fields (string decimals from API).
 * ``total_wear_bps`` is a Python @property and is not in the JSON wire payload
 * (asdict drops it) — always recompute client-side with fixed precision.
 */
export function totalWearBps(costs: {
  bybit_taker_bps: string;
  fluxion_fee_bps: string;
  bybit_slip_bps: string;
  fluxion_slip_bps: string;
  gas_bps: string;
  basis_bps: string;
  withdrawal_fee_bps?: string;
} | null | undefined): string | null {
  if (!costs) return null;
  const parts = [
    costs.bybit_taker_bps,
    costs.fluxion_fee_bps,
    costs.bybit_slip_bps,
    costs.fluxion_slip_bps,
    costs.gas_bps,
    costs.basis_bps,
    costs.withdrawal_fee_bps ?? "0",
  ].map(parseNum);
  if (parts.some((p) => p === null)) return null;
  const nums = parts as number[];
  const sum = nums.reduce((a, b) => a + b, 0);
  // Avoid float tail like 18.500000000000004 — fixed-point style for wire parity.
  return Number(sum.toFixed(4)).toString();
}
