"use client";

/**
 * MM inventory curves + rebalance timeline (WHI-769).
 * Polls GET /api/pairs/{id}/mm independently of the main detail payload.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import { EmptyPanel } from "@/components/ui/empty-panel";
import { Badge } from "@/components/ui/badge";
import { fetchJson } from "@/lib/api";
import {
  explorerTxUrl,
  fmtNotional,
  fmtTsMs,
  shortAddr,
} from "@/lib/format";
import {
  alignInventorySeries,
  prepareInventorySeries,
} from "@/lib/inventory-chart";
import { mmEmptyMessage } from "@/lib/mm";
import type { MmPairResponse } from "@/lib/types";

const COLORS = [
  "hsl(199 80% 55%)",
  "hsl(142 55% 45%)",
  "hsl(38 80% 55%)",
  "hsl(280 55% 60%)",
  "hsl(0 65% 55%)",
];
const AXIS = "hsl(215 12% 58%)";
const GRID = "hsla(220, 10%, 40%, 0.25)";

type Props = {
  pairId: string;
  pollMs: number;
};

export function MmPanel({ pairId, pollMs }: Props) {
  const [data, setData] = useState<MmPairResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const d = await fetchJson<MmPairResponse>(
        `/api/pairs/${encodeURIComponent(pairId)}/mm`,
      );
      setData(d);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [pairId]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => {
      void refresh();
    }, pollMs);
    return () => window.clearInterval(id);
  }, [refresh, pollMs]);

  if (!data && err) {
    return (
      <EmptyPanel message={`MM panel unavailable: ${err}`} />
    );
  }
  if (!data) {
    return (
      <p className="text-xs text-muted-foreground">Loading MM panel…</p>
    );
  }

  // Always surface rebalance txs when present (even on no_candidates /
  // accumulating) so explorer links stay verifiable.
  const showEmpty = data.status !== "ok" && data.addresses.length === 0;

  return (
    <div className="space-y-4">
      {err && (
        <p className="text-[11px] text-warning">
          Refresh failed — showing last snapshot. {err}
        </p>
      )}
      {showEmpty ? (
        <EmptyPanel
          variant="solid"
          message={mmEmptyMessage(data.status)}
        />
      ) : (
        <InventoryChart addresses={data.addresses} />
      )}
      <RebalanceTimeline events={data.rebalance_events} />
    </div>
  );
}

function InventoryChart({
  addresses,
}: {
  addresses: MmPairResponse["addresses"];
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  /** Stable handle for setData — updated every render without recreating plot. */
  const seriesRef = useRef(prepareInventorySeries(addresses));
  const series = useMemo(() => prepareInventorySeries(addresses), [addresses]);
  seriesRef.current = series;

  const empty = series.length === 0;
  // Shape key: recreate plot only when address set changes (not every poll).
  const shapeKey = useMemo(
    () => series.map((s) => s.address).join("|"),
    [series],
  );
  // Data key: setData on poll without full remount (spread-chart pattern).
  const dataKey = useMemo(() => {
    if (series.length === 0) return "";
    return series
      .map((s) => `${s.address}:${s.xs.length}:${s.ys[s.ys.length - 1]}`)
      .join("|");
  }, [series]);

  // Create plot once per address set (or empty ↔ non-empty).
  useEffect(() => {
    if (empty || !hostRef.current) {
      plotRef.current?.destroy();
      plotRef.current = null;
      return;
    }

    const s0 = seriesRef.current;
    const aligned = alignInventorySeries(s0);
    const data: uPlot.AlignedData = [aligned.xs, ...aligned.columns];

    const el = hostRef.current;
    const width = Math.max(el.clientWidth || 640, 320);
    const height = 200;

    const opts: uPlot.Options = {
      width,
      height,
      class: "mm-inv-uplot",
      cursor: { show: true, points: { size: 5 } },
      legend: { show: true },
      scales: { x: { time: true }, y: { auto: true } },
      axes: [
        {
          stroke: AXIS,
          grid: { stroke: GRID },
          ticks: { stroke: GRID },
          font: "11px ui-monospace, Menlo, monospace",
        },
        {
          stroke: AXIS,
          grid: { stroke: GRID },
          ticks: { stroke: GRID },
          font: "11px ui-monospace, Menlo, monospace",
          size: 48,
        },
      ],
      series: [
        {},
        ...s0.map((s, i) => ({
          label: s.shortLabel,
          stroke: COLORS[i % COLORS.length],
          width: 1.5,
          points: { show: s.xs.length < 40 },
          // Inventory is forward-filled in alignInventorySeries — no gaps.
          spanGaps: false,
        })),
      ],
    };

    plotRef.current?.destroy();
    plotRef.current = new uPlot(opts, data, el);

    const ro = new ResizeObserver(() => {
      if (!hostRef.current || !plotRef.current) return;
      plotRef.current.setSize({
        width: Math.max(hostRef.current.clientWidth || 640, 320),
        height,
      });
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      plotRef.current?.destroy();
      plotRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- shape only
  }, [empty, shapeKey]);

  // setData on poll (preserve cursor / zoom).
  useEffect(() => {
    if (!plotRef.current || empty) return;
    const aligned = alignInventorySeries(seriesRef.current);
    plotRef.current.setData([aligned.xs, ...aligned.columns]);
  }, [dataKey, empty]);

  if (empty) {
    return (
      <EmptyPanel
        variant="solid"
        message="Market makers labeled, but no inventory events yet for this pair."
      />
    );
  }

  return (
    <div>
      <h3 className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        Inventory (native units, cumulative)
      </h3>
      <div className="mb-1 flex flex-wrap gap-2 text-[10px] text-muted-foreground">
        {addresses.map((a) => (
          <span key={a.address} className="inline-flex items-center gap-1">
            <Badge variant="mm" className="normal-case">
              {shortAddr(a.address)}
            </Badge>
            final={fmtNotional(a.final_inventory)}
            {a.is_rebalancer && (
              <Badge variant="warning" className="normal-case">
                reb
              </Badge>
            )}
          </span>
        ))}
      </div>
      <div ref={hostRef} className="w-full" />
    </div>
  );
}

function RebalanceTimeline({
  events,
}: {
  events: MmPairResponse["rebalance_events"];
}) {
  return (
    <div>
      <h3 className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        Rebalance events
      </h3>
      {events.length === 0 ? (
        <EmptyPanel
          variant="solid"
          message="No CEX-touch rebalance events recorded for this pair."
        />
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full min-w-[560px] border-collapse text-xs">
            <thead>
              <tr className="border-b border-border bg-muted/40 text-muted-foreground">
                <th className="px-2 py-1.5 text-left text-[10px] font-medium uppercase">
                  Time
                </th>
                <th className="px-2 py-1.5 text-left text-[10px] font-medium uppercase">
                  Address
                </th>
                <th className="px-2 py-1.5 text-left text-[10px] font-medium uppercase">
                  Dir
                </th>
                <th className="px-2 py-1.5 text-right text-[10px] font-medium uppercase">
                  Amount
                </th>
                <th className="px-2 py-1.5 text-left text-[10px] font-medium uppercase">
                  Tx
                </th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => {
                const url = explorerTxUrl(e.tx_hash);
                const dirLabel =
                  e.direction === "deposit_to_cex"
                    ? "→ CEX"
                    : e.direction === "withdraw_from_cex"
                      ? "← CEX"
                      : e.direction;
                return (
                  <tr
                    key={`${e.tx_hash}-${e.log_index}-${e.address}`}
                    className="border-b border-border/60 hover:bg-muted/30"
                  >
                    <td
                      className="px-2 py-1 tabular-nums text-muted-foreground"
                      title={new Date(e.recv_ts_ms).toISOString()}
                    >
                      {fmtTsMs(e.recv_ts_ms)}
                    </td>
                    <td
                      className="px-2 py-1 font-mono text-[11px]"
                      title={e.address}
                    >
                      {shortAddr(e.address)}
                    </td>
                    <td className="px-2 py-1">
                      <Badge
                        variant={
                          e.direction === "deposit_to_cex"
                            ? "warning"
                            : "positive"
                        }
                        className="normal-case"
                      >
                        {dirLabel}
                      </Badge>
                    </td>
                    <td className="px-2 py-1 text-right tabular-nums">
                      {fmtNotional(e.amount)}
                    </td>
                    <td className="px-2 py-1 font-mono text-[11px]">
                      {url ? (
                        <a
                          href={url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-sky-300 hover:underline underline-offset-2"
                          onClick={(ev) => ev.stopPropagation()}
                        >
                          {shortAddr(e.tx_hash)}
                        </a>
                      ) : (
                        shortAddr(e.tx_hash)
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
