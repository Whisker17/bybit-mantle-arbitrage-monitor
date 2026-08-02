/**
 * MM panel helpers (WHI-769): overview badge + empty-state copy.
 */

import type { MmActiveStatus, MmDataStatus } from "./types";

export function mmActiveLabel(status: MmActiveStatus | null | undefined): string {
  if (status === "active") return "MM";
  if (status === "inactive") return "—";
  return "?";
}

export function mmActiveTitle(
  status: MmActiveStatus | null | undefined,
): string {
  if (status === "active") {
    return "Market maker activity on this pair in the last 24h";
  }
  if (status === "inactive") {
    return "No market_maker activity on this pair in the last 24h";
  }
  return "MM labels not yet available (attribution refresh pending)";
}

export function mmEmptyMessage(status: MmDataStatus): string {
  if (status === "accumulating") {
    return "Data accumulating — address labels not yet written. Run attribution refresh or wait for the collector loop.";
  }
  if (status === "no_candidates") {
    return "No market_maker candidates for this pair (labels exist, none match inventory on this pair).";
  }
  return "";
}
