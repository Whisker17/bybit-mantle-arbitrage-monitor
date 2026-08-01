/**
 * Pure prep for the detail spread chart (uPlot series).
 * Keep free of React so unit tests can pin the shape without a DOM.
 */

import { parseNum } from "./format";
import type { SessionKind, SpreadPoint } from "./types";

export type SpreadChartSeries = {
  /** Unix seconds (uPlot x). */
  xs: number[];
  /** AMM spread bps; null gaps become null for uPlot. */
  amm: (number | null)[];
  rfq: (number | null)[];
  bybitMid: (number | null)[];
  sessions: SessionKind[];
  /** Inclusive [i0, i1] index ranges painted as open-session background. */
  openBands: Array<[number, number]>;
  hasAmm: boolean;
  hasRfq: boolean;
  hasMid: boolean;
};

/** Build aligned series from API spread_series (already downsampled server-side). */
export function prepareSpreadSeries(
  points: SpreadPoint[] | null | undefined,
): SpreadChartSeries {
  if (!points || points.length === 0) {
    return {
      xs: [],
      amm: [],
      rfq: [],
      bybitMid: [],
      sessions: [],
      openBands: [],
      hasAmm: false,
      hasRfq: false,
      hasMid: false,
    };
  }

  // Ensure ascending time (builder usually already is).
  const sorted = [...points].sort((a, b) => a.ts_ms - b.ts_ms);
  const xs: number[] = [];
  const amm: (number | null)[] = [];
  const rfq: (number | null)[] = [];
  const bybitMid: (number | null)[] = [];
  const sessions: SessionKind[] = [];

  let hasAmm = false;
  let hasRfq = false;
  let hasMid = false;

  for (const p of sorted) {
    xs.push(p.ts_ms / 1000);
    const a = parseNum(p.amm_spread_bps);
    const r = parseNum(p.rfq_spread_bps ?? null);
    const m = parseNum(p.bybit_mid ?? null);
    if (a !== null) hasAmm = true;
    if (r !== null) hasRfq = true;
    if (m !== null) hasMid = true;
    amm.push(a);
    rfq.push(r);
    bybitMid.push(m);
    sessions.push(p.session);
  }

  return {
    xs,
    amm,
    rfq,
    bybitMid,
    sessions,
    openBands: sessionOpenBands(sessions),
    hasAmm,
    hasRfq,
    hasMid,
  };
}

/** Contiguous open-session index ranges for background banding. */
export function sessionOpenBands(
  sessions: SessionKind[],
): Array<[number, number]> {
  const bands: Array<[number, number]> = [];
  let start: number | null = null;
  for (let i = 0; i < sessions.length; i++) {
    if (sessions[i] === "open") {
      if (start === null) start = i;
    } else if (start !== null) {
      bands.push([start, i - 1]);
      start = null;
    }
  }
  if (start !== null) {
    bands.push([start, sessions.length - 1]);
  }
  return bands;
}
