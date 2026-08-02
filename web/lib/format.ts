import type { Direction, SessionKind } from "./types";

const DASH = "—";

/** Badge variant for underlying price_type (WHI-779). */
export function priceTypeBadgeVariant(
  pt: string | null | undefined,
): "open" | "closed" | "warning" | "muted" {
  if (pt === "live") return "open";
  if (pt === "stale") return "warning";
  if (pt === "pre" || pt === "post" || pt === "close") return "closed";
  return "muted";
}

export function fmtPrice(
  value: string | number | null | undefined,
  digits = 4,
): string {
  if (value === null || value === undefined || value === "") return DASH;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
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
 * Prefer API ``cex_venue``/``dex_venue`` when available.
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

/** Full-words tooltip for Dir cells (acceptance: tooltips explain direction). */
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
    return `Buy ${dex}, sell ${cex} (wire: buy_fluxion_sell_bybit)`;
  }
  return `Buy ${cex}, sell ${dex} (wire: buy_bybit_sell_fluxion)`;
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
} | null | undefined): string | null {
  if (!costs) return null;
  const parts = [
    costs.bybit_taker_bps,
    costs.fluxion_fee_bps,
    costs.bybit_slip_bps,
    costs.fluxion_slip_bps,
    costs.gas_bps,
    costs.basis_bps,
  ].map(parseNum);
  if (parts.some((p) => p === null)) return null;
  const nums = parts as number[];
  const sum = nums.reduce((a, b) => a + b, 0);
  // Avoid float tail like 18.500000000000004 — fixed-point style for wire parity.
  return Number(sum.toFixed(4)).toString();
}
