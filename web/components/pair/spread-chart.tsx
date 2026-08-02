"use client";

/**
 * Lightweight uPlot chart for AMM/RFQ spread vs time.
 * Canvas path handles 1000+ points; setData on poll (no full remount).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import { EmptyPanel } from "@/components/ui/empty-panel";
import { cn } from "@/lib/cn";
import { prepareSpreadSeries, type SpreadChartSeries } from "@/lib/spread-chart";
import type { SpreadPoint } from "@/lib/types";

type Props = {
  points: SpreadPoint[];
  className?: string;
  /** When false, RFQ series is omitted (AMM-only markets, WHI-774). */
  showRfq?: boolean;
};

/** Match Tailwind theme tokens (positive / primary-ish blue / warning). */
const AMM_COLOR = "hsl(142 55% 45%)";
const RFQ_COLOR = "hsl(210 70% 55%)";
const MID_COLOR = "hsl(38 80% 55%)";
const OPEN_BAND = "hsla(142, 40%, 30%, 0.18)";
const CLOSED_BAND = "hsla(38, 50%, 30%, 0.12)";
const AXIS = "hsl(215 12% 58%)";
const GRID = "hsla(220, 10%, 40%, 0.25)";

export function SpreadChart({ points, className, showRfq = true }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  /** Stable handle for draw hook — updated every render without recreating plot. */
  const seriesRef = useRef<SpreadChartSeries | null>(null);
  const [showMid, setShowMid] = useState(false);

  const series = useMemo(() => prepareSpreadSeries(points), [points]);
  seriesRef.current = series;
  const empty =
    series.xs.length === 0 ||
    (!series.hasAmm && !(showRfq && series.hasRfq));

  // Fingerprint data for setData (identity of `points` array changes every poll).
  const dataKey = useMemo(() => {
    if (series.xs.length === 0) return "";
    const last = series.xs.length - 1;
    return `${series.xs.length}:${series.xs[0]}:${series.xs[last]}:${series.amm[last]}:${series.rfq[last]}`;
  }, [series]);

  // Create plot once (or when empty ↔ non-empty / showMid scale changes).
  useEffect(() => {
    if (empty || !hostRef.current) {
      plotRef.current?.destroy();
      plotRef.current = null;
      return;
    }

    const el = hostRef.current;
    const width = Math.max(el.clientWidth || 640, 320);
    const height = 220;
    const s0 = seriesRef.current!;

    const opts: uPlot.Options = {
      width,
      height,
      class: "spread-uplot",
      cursor: { show: true, points: { size: 6 } },
      legend: { show: true },
      scales: {
        x: { time: true },
        bps: { auto: true },
        mid: { auto: true },
      },
      axes: [
        {
          stroke: AXIS,
          grid: { stroke: GRID },
          ticks: { stroke: GRID },
          font: "11px ui-monospace, Menlo, monospace",
        },
        {
          scale: "bps",
          stroke: AXIS,
          grid: { stroke: GRID },
          ticks: { stroke: GRID },
          font: "11px ui-monospace, Menlo, monospace",
          size: 48,
          label: "bps",
          labelSize: 12,
          labelFont: "10px ui-monospace, Menlo, monospace",
        },
        {
          scale: "mid",
          side: 1,
          stroke: MID_COLOR,
          grid: { show: false },
          ticks: { stroke: GRID },
          font: "11px ui-monospace, Menlo, monospace",
          size: showMid && s0.hasMid ? 52 : 0,
          show: showMid && s0.hasMid,
          label: showMid ? "mid" : undefined,
          labelSize: 12,
          labelFont: "10px ui-monospace, Menlo, monospace",
        },
      ],
      series: [
        {},
        {
          label: "AMM",
          stroke: AMM_COLOR,
          width: 1.5,
          scale: "bps",
          spanGaps: false,
          points: { show: false },
          show: s0.hasAmm,
        },
        {
          label: "RFQ",
          stroke: RFQ_COLOR,
          width: 1.5,
          scale: "bps",
          spanGaps: false,
          points: { show: false },
          show: showRfq && s0.hasRfq,
        },
        {
          label: "CEX mid",
          stroke: MID_COLOR,
          width: 1,
          dash: [4, 3],
          scale: "mid",
          spanGaps: false,
          points: { show: false },
          show: showMid && s0.hasMid,
        },
      ],
      hooks: {
        drawClear: [
          (u) => {
            const s = seriesRef.current;
            if (!s || s.xs.length === 0) return;
            const { ctx } = u;
            const { left, top, width: w, height: h } = u.bbox;
            ctx.save();
            ctx.fillStyle = CLOSED_BAND;
            ctx.fillRect(left, top, w, h);
            for (const [i0, i1] of s.openBands) {
              const x0 = u.valToPos(s.xs[i0]!, "x", true);
              const x1 = u.valToPos(s.xs[i1]!, "x", true);
              const xL = Math.min(x0, x1);
              const xR = Math.max(x0, x1);
              // Pad only single-point open bands so a 1-sample open is visible.
              const pad = i0 === i1 ? 2 : 0;
              ctx.fillStyle = OPEN_BAND;
              ctx.fillRect(xL - pad, top, Math.max(xR - xL, 1) + pad * 2, h);
            }
            ctx.restore();
          },
        ],
      },
    };

    plotRef.current?.destroy();
    plotRef.current = new uPlot(opts, [s0.xs, s0.amm, s0.rfq, s0.bybitMid], el);

    const ro = new ResizeObserver(() => {
      if (!hostRef.current || !plotRef.current) return;
      plotRef.current.setSize({
        width: Math.max(hostRef.current.clientWidth, 320),
        height,
      });
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      plotRef.current?.destroy();
      plotRef.current = null;
    };
    // Recreate when empty flips, mid axis toggles, or AMM/RFQ presence changes
    // (series.show is fixed at construction; setData alone cannot unhide).
  }, [empty, showMid, showRfq, series.hasAmm, series.hasRfq, series.hasMid]);

  // Push new samples without destroying the plot (keeps zoom/cursor).
  useEffect(() => {
    if (empty || !plotRef.current || !seriesRef.current) return;
    const s = seriesRef.current;
    plotRef.current.setData([s.xs, s.amm, s.rfq, s.bybitMid]);
  }, [dataKey, empty, showMid, series.hasAmm, series.hasRfq]);

  if (empty) {
    return (
      <EmptyPanel
        className={className}
        message="No spread history yet — waiting for Bybit book + pool ticks."
      />
    );
  }

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] text-muted-foreground">
        <div className="flex flex-wrap items-center gap-3">
          <LegendSwatch color={AMM_COLOR} label="AMM bps" />
          <LegendSwatch color={RFQ_COLOR} label="RFQ bps" />
          <span className="inline-flex items-center gap-1">
            <span
              className="inline-block h-2.5 w-3 rounded-sm"
              style={{ background: OPEN_BAND, border: `1px solid ${AMM_COLOR}` }}
            />
            open
          </span>
          <span className="inline-flex items-center gap-1">
            <span
              className="inline-block h-2.5 w-3 rounded-sm"
              style={{ background: CLOSED_BAND }}
            />
            closed
          </span>
          <span className="tabular-nums">n={series.xs.length}</span>
        </div>
        <label className="inline-flex cursor-pointer items-center gap-1.5">
          <input
            type="checkbox"
            className="accent-primary"
            checked={showMid}
            disabled={!series.hasMid}
            onChange={(e) => setShowMid(e.target.checked)}
          />
          overlay Bybit mid
        </label>
      </div>
      <div
        ref={hostRef}
        className="w-full overflow-hidden rounded-md border border-border bg-card/40 px-1 pt-1"
      />
    </div>
  );
}

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span
        className="inline-block h-0.5 w-3 rounded-full"
        style={{ background: color }}
      />
      {label}
    </span>
  );
}

