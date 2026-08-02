/**
 * Prepare MM inventory series for uPlot (WHI-769).
 * One address per series; xs shared when timestamps align loosely —
 * we plot each address independently with its own xs.
 */

import type { MmAddressSeries } from "./types";

export type InventoryChartSeries = {
  address: string;
  shortLabel: string;
  xs: number[];
  ys: (number | null)[];
  finalInventory: number | null;
};

function shortAddr(addr: string): string {
  if (addr.length < 12) return addr;
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

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
