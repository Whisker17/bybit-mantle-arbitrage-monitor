"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import {
  bpsTone,
  fmtDirection,
  fmtNotional,
  fmtPrice,
  fmtSession,
  fmtSignedBps,
  fmtUsd,
  usdTone,
} from "@/lib/format";
import { mmActiveLabel, mmActiveTitle } from "@/lib/mm";
import { overviewPnlCell } from "@/lib/pnl";
import type { MmActiveStatus, PairOverviewRow, SortKey } from "@/lib/types";

type Props = {
  rows: PairOverviewRow[];
  sortKey: SortKey;
  sortDesc: boolean;
  onSort: (key: SortKey) => void;
};

type Col = {
  key: SortKey | null;
  label: string;
  align?: "left" | "right";
  title?: string;
};

const COLS: Col[] = [
  { key: "pair_id", label: "Pair" },
  { key: null, label: "Sess", title: "NYSE session at last quote" },
  { key: null, label: "Bid", align: "right" },
  { key: null, label: "Ask", align: "right" },
  { key: "bybit_mid", label: "Mid", align: "right" },
  { key: null, label: "AMM", align: "right", title: "Fluxion AMM mid" },
  { key: null, label: "RFQb", align: "right", title: "RFQ buy (taker buys base)" },
  { key: null, label: "RFQs", align: "right", title: "RFQ sell" },
  { key: "amm_spread", label: "AMM bps", align: "right" },
  { key: "rfq_spread", label: "RFQ bps", align: "right" },
  { key: "net_edge", label: "Net", align: "right", title: "Net edge at ref size (AMM)" },
  { key: null, label: "Dir", title: "Arb direction" },
  { key: null, label: "Ven" },
  { key: "volume_24h", label: "Vol24h", align: "right" },
  { key: "trades_24h", label: "N24h", align: "right" },
  {
    key: null,
    label: "Bucket PnL",
    align: "right",
    title: "Optimal size net PnL (PnL v2) — hover for direction & notional",
  },
  {
    key: null,
    label: "MM",
    title:
      "Market-maker trade activity in lookback window: active / inactive / unknown",
  },
];

function BpsCell({ value }: { value: string | null }) {
  const tone = bpsTone(value);
  return (
    <span
      className={cn(
        "tabular-nums",
        tone === "pos" && "text-positive",
        tone === "neg" && "text-negative",
        tone === "empty" && "text-muted-foreground",
      )}
    >
      {fmtSignedBps(value)}
    </span>
  );
}

function BucketPnlCell({ row }: { row: PairOverviewRow }) {
  const cell = overviewPnlCell(row.pnl_v2);
  if (cell.kind === "ok") {
    const tone = usdTone(cell.pnlUsd);
    return (
      <span
        className={cn(
          "tabular-nums font-medium",
          tone === "pos" && "text-positive",
          tone === "neg" && "text-negative",
          tone === "empty" && "text-muted-foreground",
        )}
        title={cell.title}
      >
        {fmtUsd(cell.pnlUsd)}
      </span>
    );
  }
  return (
    <span className="text-muted-foreground" title={cell.title}>
      {cell.label}
    </span>
  );
}

function MmActiveCell({ status }: { status: MmActiveStatus | null | undefined }) {
  const s = status ?? "unknown";
  if (s === "active") {
    return (
      <Badge variant="mm" className="normal-case" title={mmActiveTitle(s)}>
        {mmActiveLabel(s)}
      </Badge>
    );
  }
  return (
    <span className="text-muted-foreground" title={mmActiveTitle(s)}>
      {mmActiveLabel(s)}
    </span>
  );
}

export function PairsTable({ rows, sortKey, sortDesc, onSort }: Props) {
  const router = useRouter();

  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full min-w-[1160px] border-collapse text-xs">
        <thead>
          <tr className="border-b border-border bg-muted/40 text-muted-foreground">
            {COLS.map((col) => {
              const sortable = col.key != null;
              const active = col.key === sortKey;
              const arrow = active ? (sortDesc ? " ↓" : " ↑") : "";
              return (
                <th
                  key={col.label}
                  title={col.title}
                  className={cn(
                    "whitespace-nowrap px-2 py-2 font-medium",
                    col.align === "right" ? "text-right" : "text-left",
                    sortable &&
                      "cursor-pointer select-none hover:text-foreground",
                    active && "text-foreground",
                  )}
                  onClick={() => {
                    if (col.key) onSort(col.key);
                  }}
                  aria-sort={
                    active ? (sortDesc ? "descending" : "ascending") : undefined
                  }
                >
                  {col.label}
                  {arrow}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={COLS.length}
                className="px-3 py-8 text-center text-muted-foreground"
              >
                No pairs match the current filter.
              </td>
            </tr>
          ) : (
            rows.map((row) => {
              const dim = row.low_liquidity || row.stale;
              const href = `/pair/${row.pair_id}/`;
              return (
                <tr
                  key={row.pair_id}
                  className={cn(
                    "border-b border-border/60 transition-colors hover:bg-muted/50 cursor-pointer",
                    dim && "opacity-50",
                  )}
                  onClick={() => {
                    // Don't navigate when the operator is drag-selecting a price to copy.
                    const sel = window.getSelection();
                    if (sel && !sel.isCollapsed && sel.toString().length > 0) {
                      return;
                    }
                    router.push(href);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      router.push(href);
                    }
                  }}
                  tabIndex={0}
                >
                  <td className="px-2 py-1.5 font-medium text-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      <Link
                        href={href}
                        className="hover:underline underline-offset-2 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded-sm"
                        onClick={(e) => {
                          // Let the row handler navigate; avoid double push.
                          e.stopPropagation();
                        }}
                      >
                        {row.pair_id}
                      </Link>
                      {row.low_liquidity && (
                        <Badge variant="muted" className="normal-case">
                          low-liq
                        </Badge>
                      )}
                      {row.stale && (
                        <Badge variant="warning" className="normal-case">
                          stale
                        </Badge>
                      )}
                    </span>
                  </td>
                  <td className="px-2 py-1.5">
                    <Badge
                      variant={
                        row.session === "open"
                          ? "open"
                          : row.session === "closed"
                            ? "closed"
                            : "muted"
                      }
                    >
                      {fmtSession(row.session)}
                    </Badge>
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {fmtPrice(row.bybit_bid)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {fmtPrice(row.bybit_ask)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {fmtPrice(row.bybit_mid)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {fmtPrice(row.amm_mid)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {fmtPrice(row.rfq_buy)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {fmtPrice(row.rfq_sell)}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <BpsCell value={row.amm_spread_bps} />
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <BpsCell value={row.rfq_spread_bps} />
                  </td>
                  <td className="px-2 py-1.5 text-right font-medium">
                    <BpsCell value={row.net_edge_bps} />
                  </td>
                  <td className="px-2 py-1.5 text-muted-foreground">
                    {fmtDirection(row.net_edge_direction)}
                  </td>
                  <td className="px-2 py-1.5 uppercase text-muted-foreground">
                    {row.net_edge_venue ?? "—"}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {fmtNotional(row.volume_24h)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {row.trades_24h}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <BucketPnlCell row={row} />
                  </td>
                  <td className="px-2 py-1.5">
                    <MmActiveCell status={row.mm_active} />
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
