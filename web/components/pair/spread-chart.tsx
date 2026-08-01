"use client";

/**
 * Lightweight uPlot chart for AMM/RFQ spread vs time.
 * Canvas path handles 1000+ points without React re-render per point.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import { prepareSpreadSeries } from "@/lib/spread-chart";
import type { SpreadPoint } from "@/lib/types";
import { cn } from "@/lib/cn";

type Props = {
  points: SpreadPoint[];
  className?: string;
};

const AMM_COLOR = "hsl(142 55% 45%)";
const RFQ_COLOR = "hsl(210 70% 55%)";
const MID_COLOR = "hsl(38 80% 55%)";
const OPEN_BAND = "hsla(142, 40%, 30%, 0.18)";
const CLOSED_BAND = "hsla(38, 50%, 30%, 0.12)";
const AXIS = "hsl(215 12% 58%)";
const GRID = "hsla(220, 10%, 40%, 0.25)";

export function SpreadChart({ points, className }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const [showMid, setShowMid] = useState(false);

  const series = useMemo(() => prepareSpreadSeries(points), [points]);
  const empty = series.xs.length === 0 || (!series.hasAmm && !series.hasRfq);

  useEffect(() => {
    if (empty || !hostRef.current) {
      plotRef.current?.destroy();
      plotRef.current = null;
      return;
    }

    const el = hostRef.current;
    const width = Math.max(el.clientWidth || 640, 320);
    const height = 220;

    const data: uPlot.AlignedData = [
      series.xs,
      series.amm,
      series.rfq,
      series.bybitMid,
    ];

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
          size: showMid && series.hasMid ? 52 : 0,
          show: showMid && series.hasMid,
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
          show: series.hasAmm,
        },
        {
          label: "RFQ",
          stroke: RFQ_COLOR,
          width: 1.5,
          scale: "bps",
          spanGaps: false,
          points: { show: false },
          show: series.hasRfq,
        },
        {
          label: "Bybit mid",
          stroke: MID_COLOR,
          width: 1,
          dash: [4, 3],
          scale: "mid",
          spanGaps: false,
          points: { show: false },
          show: showMid && series.hasMid,
        },
      ],
      hooks: {
        drawClear: [
          (u) => {
            const { ctx } = u;
            const { left, top, width: w, height: h } = u.bbox;
            ctx.save();
            // Base closed-session tint across the full plot.
            ctx.fillStyle = CLOSED_BAND;
            ctx.fillRect(left, top, w, h);
            // Open bands over closed.
            for (const [i0, i1] of series.openBands) {
              const x0 = u.valToPos(series.xs[i0]!, "x", true);
              const x1 = u.valToPos(series.xs[i1]!, "x", true);
              const xL = Math.min(x0, x1);
              const xR = Math.max(x0, x1);
              // Pad a half-step so single-point open bands are visible.
              const pad = Math.max(1, (xR - xL) * 0.02 + 2);
              ctx.fillStyle = OPEN_BAND;
              ctx.fillRect(xL - pad, top, xR - xL + pad * 2, h);
            }
            ctx.restore();
          },
        ],
      },
    };

    plotRef.current?.destroy();
    plotRef.current = new uPlot(opts, data, el);

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
  }, [series, showMid, empty]);

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
              style={{ background: OPEN_BAND, border: "1px solid " + AMM_COLOR }}
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

export function EmptyPanel({
  message,
  className,
}: {
  message: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-md border border-dashed border-border px-3 py-6 text-center text-[11px] text-muted-foreground",
        className,
      )}
    >
      {message}
    </div>
  );
}
