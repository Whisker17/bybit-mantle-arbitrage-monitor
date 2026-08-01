import type { Direction, SessionKind } from "./types";

const DASH = "—";

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

export function fmtDirection(direction: Direction | null | undefined): string {
  if (!direction) return DASH;
  if (direction === "buy_fluxion_sell_bybit") return "F→B";
  return "B→F";
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

/** Mantle mainnet explorer link for a transaction hash. */
export function explorerTxUrl(txHash: string | null | undefined): string | null {
  if (!txHash) return null;
  const h = txHash.startsWith("0x") ? txHash : `0x${txHash}`;
  return `https://mantlescan.xyz/tx/${h}`;
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

/** Sum of CostBreakdown wear fields (string decimals from API). */
export function totalWearBps(costs: {
  bybit_taker_bps: string;
  fluxion_fee_bps: string;
  bybit_slip_bps: string;
  fluxion_slip_bps: string;
  gas_bps: string;
  basis_bps: string;
  total_wear_bps?: string;
} | null | undefined): string | null {
  if (!costs) return null;
  if (costs.total_wear_bps != null && costs.total_wear_bps !== "") {
    return costs.total_wear_bps;
  }
  const parts = [
    costs.bybit_taker_bps,
    costs.fluxion_fee_bps,
    costs.bybit_slip_bps,
    costs.fluxion_slip_bps,
    costs.gas_bps,
    costs.basis_bps,
  ].map(parseNum);
  if (parts.some((p) => p === null)) return null;
  return String(parts.reduce((a, b) => (a as number) + (b as number), 0));
}
