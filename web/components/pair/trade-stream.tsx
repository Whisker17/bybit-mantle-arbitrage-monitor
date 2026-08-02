"use client";

import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { EmptyPanel } from "@/components/ui/empty-panel";
import { cn } from "@/lib/cn";
import {
  explorerTxUrl,
  fmtDirection,
  fmtDirectionTitle,
  fmtLabel,
  fmtNotional,
  fmtPrice,
  fmtTsMs,
  shortAddr,
} from "@/lib/format";
import type { TradeStreamRow } from "@/lib/types";

type Props = {
  trades: TradeStreamRow[];
  /** Market id for venue-aware Dir codes (WHI-780). */
  marketId?: string;
};

export function TradeStream({ trades, marketId }: Props) {
  if (trades.length === 0) {
    return (
      <EmptyPanel message="No Fluxion fills in the detail window (or RFQ fills lack pair_id — see DEFERRED_ISSUES)." />
    );
  }

  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <div className="max-h-[320px] overflow-y-auto">
        <table className="w-full min-w-[880px] border-collapse text-xs">
          <thead className="sticky top-0 z-10 bg-muted/90 backdrop-blur">
            <tr className="border-b border-border text-muted-foreground">
              <Th>Time</Th>
              <Th>Mech</Th>
              <Th>Dir</Th>
              <Th align="right">Notional</Th>
              <Th align="right">Fill</Th>
              <Th align="right">Bybit</Th>
              <Th>Conv</Th>
              <Th>Taker</Th>
              <Th>Label</Th>
              <Th>Tx</Th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => {
              const txUrl = explorerTxUrl(t.tx_hash);
              const conv =
                t.converging === null
                  ? "—"
                  : t.converging
                    ? "yes"
                    : "no";
              return (
                <tr
                  key={`${t.tx_hash}-${t.ts_ms}-${i}`}
                  className="border-b border-border/60 hover:bg-muted/30"
                >
                  <Td className="tabular-nums text-muted-foreground">
                    {fmtTsMs(t.ts_ms)}
                  </Td>
                  <Td>
                    <Badge
                      variant={t.mechanism === "rfq" ? "warning" : "muted"}
                    >
                      {t.mechanism}
                    </Badge>
                  </Td>
                  <Td
                    className="tabular-nums"
                    title={
                      t.direction === "buy_fluxion_sell_bybit" ||
                      t.direction === "buy_bybit_sell_fluxion"
                        ? fmtDirectionTitle(t.direction, null, marketId)
                        : undefined
                    }
                  >
                    {t.direction === "buy_fluxion_sell_bybit" ||
                    t.direction === "buy_bybit_sell_fluxion"
                      ? fmtDirection(t.direction, null, marketId)
                      : t.direction || "—"}
                  </Td>
                  <Td align="right" className="tabular-nums">
                    {fmtNotional(t.notional_usd)}
                  </Td>
                  <Td align="right" className="tabular-nums">
                    {fmtPrice(t.price)}
                  </Td>
                  <Td align="right" className="tabular-nums">
                    {fmtPrice(t.bybit_mid)}
                  </Td>
                  <Td
                    className={cn(
                      t.converging === true && "text-positive",
                      t.converging === false && "text-negative",
                    )}
                  >
                    {conv}
                  </Td>
                  <Td
                    className="font-mono text-[11px]"
                    title={t.taker ?? undefined}
                  >
                    {shortAddr(t.taker)}
                  </Td>
                  <Td className="text-[11px] text-muted-foreground">
                    {fmtLabel(t.taker_label)}
                  </Td>
                  <Td className="font-mono text-[11px]">
                    {txUrl ? (
                      <a
                        href={txUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary underline-offset-2 hover:underline"
                        title={t.tx_hash}
                      >
                        {shortAddr(t.tx_hash, 4, 4)}
                      </a>
                    ) : (
                      shortAddr(t.tx_hash, 4, 4)
                    )}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Th({
  children,
  align = "left",
}: {
  children: ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      className={cn(
        "px-2 py-1.5 text-[10px] font-medium uppercase tracking-wide",
        align === "right" ? "text-right" : "text-left",
      )}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align = "left",
  className,
  title,
}: {
  children: ReactNode;
  align?: "left" | "right";
  className?: string;
  title?: string;
}) {
  return (
    <td
      title={title}
      className={cn(
        "px-2 py-1",
        align === "right" ? "text-right" : "text-left",
        className,
      )}
    >
      {children}
    </td>
  );
}
