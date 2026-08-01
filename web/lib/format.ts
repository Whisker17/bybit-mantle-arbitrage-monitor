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
