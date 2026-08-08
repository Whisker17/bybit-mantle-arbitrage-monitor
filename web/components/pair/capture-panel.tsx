"use client";

/**
 * Capture-rate detail panel (WHI-963): windows/day sparkline + series table.
 * Occupancy-bounded capturable $/day under single-flight — not instantaneous Net.
 */

import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import {
  captureStatusTitle,
  fmtCaptureUsdPerDay,
  fmtDirection,
  fmtSession,
  fmtUsd,
  fmtWindowsPerDay,
  resolveVenues,
  usdTone,
  venueCode,
  type DirectionVenues,
} from "@/lib/format";
import { cn } from "@/lib/cn";
import type { CapturePairSnapshot } from "@/lib/types";

function CaptureSparkline({
  points,
}: {
  points: CapturePairSnapshot["sparkline"];
}) {
  const elRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);

  useEffect(() => {
    const el = elRef.current;
    if (!el || points.length === 0) {
      plotRef.current?.destroy();
      plotRef.current = null;
      return;
    }
    const xs = points.map((p) => p.bucket_start_ms / 1000);
    const ys = points.map((p) => p.n_windows);
    const data: uPlot.AlignedData = [xs, ys];
    const opts: uPlot.Options = {
      width: el.clientWidth || 480,
      height: 120,
      cursor: { show: true },
      legend: { show: false },
      scales: {
        x: { time: true },
        y: { auto: true },
      },
      axes: [
        {
          stroke: "#6b7280",
          grid: { stroke: "#1f2937" },
          ticks: { stroke: "#374151" },
          font: "10px ui-monospace, monospace",
        },
        {
          stroke: "#6b7280",
          grid: { stroke: "#1f2937" },
          ticks: { stroke: "#374151" },
          font: "10px ui-monospace, monospace",
          size: 36,
        },
      ],
      series: [
        {},
        {
          label: "windows",
          stroke: "#34d399",
          width: 1.5,
          fill: "rgba(52, 211, 153, 0.12)",
          points: { show: points.length <= 48 },
        },
      ],
    };
    plotRef.current?.destroy();
    plotRef.current = new uPlot(opts, data, el);
    return () => {
      plotRef.current?.destroy();
      plotRef.current = null;
    };
  }, [points]);

  if (points.length === 0) {
    return (
      <p className="text-xs text-muted-foreground py-4">
        No window buckets in lookback yet.
      </p>
    );
  }
  return <div ref={elRef} className="w-full" />;
}

export function CapturePanel({
  capture,
  marketId,
}: {
  capture: CapturePairSnapshot | null | undefined;
  marketId: string;
}) {
  const venues: DirectionVenues = resolveVenues(undefined, marketId);

  if (capture == null) {
    return (
      <p className="text-xs text-muted-foreground">
        Capture rate not available on this API build.
      </p>
    );
  }

  if (capture.status !== "ok") {
    return (
      <p className="text-xs text-muted-foreground">
        {captureStatusTitle(capture.status) ?? capture.status}
      </p>
    );
  }

  const tone = usdTone(capture.capturable_usd_per_day);
  const lookbackH = Math.round(capture.lookback_ms / 3_600_000);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
        <div>
          <span className="text-muted-foreground text-xs mr-1.5">Cap $/d</span>
          <span
            className={cn(
              "tabular-nums font-medium",
              tone === "pos" && "text-positive",
              tone === "neg" && "text-negative/60",
            )}
          >
            {fmtCaptureUsdPerDay(capture.capturable_usd_per_day)}
          </span>
        </div>
        <div>
          <span className="text-muted-foreground text-xs mr-1.5">Windows/d</span>
          <span className="tabular-nums">
            {fmtWindowsPerDay(capture.windows_per_day)}
          </span>
        </div>
        <div className="text-xs text-muted-foreground">
          {capture.n_windows ?? 0} windows · lookback {lookbackH}h · size $
          {capture.size_usd} · flight{" "}
          {Math.round(capture.trade_duration_ms / 1000)}s + cooldown{" "}
          {Math.round(capture.reentry_cooldown_ms / 1000)}s
        </div>
      </div>

      <div>
        <p className="text-xs font-semibold mb-1">Windows by hour</p>
        <CaptureSparkline points={capture.sparkline} />
      </div>

      {capture.series.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-muted-foreground border-b border-border/60">
                <th className="py-1 pr-2 font-medium">Dir</th>
                <th className="py-1 pr-2 font-medium">Sess</th>
                <th className="py-1 pr-2 font-medium">Ven</th>
                <th className="py-1 pr-2 font-medium text-right">Win</th>
                <th className="py-1 pr-2 font-medium text-right">Win/d</th>
                <th className="py-1 font-medium text-right">Cap $/d</th>
              </tr>
            </thead>
            <tbody>
              {capture.series.map((s) => (
                <tr
                  key={`${s.direction}-${s.session}-${s.venue}`}
                  className="border-b border-border/40"
                >
                  <td className="py-1 pr-2">
                    {fmtDirection(s.direction, venues, marketId)}
                  </td>
                  <td className="py-1 pr-2">{fmtSession(s.session)}</td>
                  <td className="py-1 pr-2">{venueCode(s.venue)}</td>
                  <td className="py-1 pr-2 text-right tabular-nums">
                    {s.n_windows}
                  </td>
                  <td className="py-1 pr-2 text-right tabular-nums">
                    {fmtWindowsPerDay(s.windows_per_day)}
                  </td>
                  <td
                    className={cn(
                      "py-1 text-right tabular-nums",
                      usdTone(s.capturable_usd_per_day) === "pos" &&
                        "text-positive",
                    )}
                  >
                    {fmtUsd(s.capturable_usd_per_day)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
