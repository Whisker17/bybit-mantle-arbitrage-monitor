/**
 * Prepare MM inventory series for uPlot (WHI-769).
 * Aligns multiple addresses onto a shared time axis (union of timestamps).
 */

import { shortAddr } from "./format";
import type { MmAddressSeries } from "./types";

export type InventoryChartSeries = {
  address: string;
  shortLabel: string;
  xs: number[];
  ys: (number | null)[];
  finalInventory: number | null;
};

/** Per-address series (own xs) before alignment. */
export function prepareInventorySeries(
  addresses: MmAddressSeries[],
): InventoryChartSeries[] {
  return addresses
    .filter((a) => a.series.length > 0)
    .map((a) => {
      const xs: number[] = [];
      const ys: (number | null)[] = [];
      for (const p of a.series) {
        const inv = Number(p.inventory);
        xs.push(p.ts_ms / 1000);
        ys.push(Number.isFinite(inv) ? inv : null);
      }
      const final = Number(a.final_inventory);
      return {
        address: a.address,
        shortLabel: shortAddr(a.address),
        xs,
        ys,
        finalInventory: Number.isFinite(final) ? final : null,
      };
    });
}

/**
 * Union timestamps → one x-axis + sparse y columns for multi-line uPlot.
 * Matches the spread-chart pure-helper pattern.
 */
export function alignInventorySeries(
  series: InventoryChartSeries[],
): { xs: number[]; columns: (number | null)[][] } {
  if (series.length === 0) return { xs: [], columns: [] };
  const allTs = new Set<number>();
  for (const s of series) {
    for (const x of s.xs) allTs.add(x);
  }
  const xs = Array.from(allTs).sort((a, b) => a - b);
  const columns = series.map((s) => {
    const map = new Map(s.xs.map((x, i) => [x, s.ys[i]]));
    return xs.map((t) => map.get(t) ?? null);
  });
  return { xs, columns };
}
