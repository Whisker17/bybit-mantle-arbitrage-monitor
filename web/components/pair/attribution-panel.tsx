"use client";

import { EmptyPanel } from "@/components/pair/spread-chart";
import {
  fmtLabel,
  fmtNotional,
  fmtPct,
  shortAddr,
} from "@/lib/format";
import type { PairAttribution, PairDetailResponse } from "@/lib/types";
import { cn } from "@/lib/cn";

type Props = {
  attribution: PairAttribution | null;
  detail: Pick<
    PairDetailResponse,
    "arb_bot_trade_share" | "price_keeper_trade_share" | "rfq_mechanism_share"
  >;
};

export function AttributionPanel({ attribution, detail }: Props) {
  if (attribution == null) {
    return (
      <EmptyPanel message="No attribution yet — no AMM trades scored in the detail window." />
    );
  }

  const m = attribution.mechanism;
  const total = m.amm_trades + m.rfq_trades;
  // Pair-scoped RFQ fills are often 0 until enrichment (DEFERRED); avoid a
  // false "0% RFQ" ring — surface n/a when rfq_trades == 0 and share is null.
  const rfqShareTxt =
    m.rfq_trades === 0
      ? "n/a (fills unscoped)"
      : fmtPct(detail.rfq_mechanism_share ?? m.rfq_trades / Math.max(total, 1));

  const top = attribution.top_takers.slice(0, 10);

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-[140px_1fr]">
        <MechanismDonut
          amm={m.amm_trades}
          rfq={m.rfq_trades}
          rfqShareLabel={rfqShareTxt}
        />
        <div className="space-y-1 text-xs">
          <p>
            <span className="text-muted-foreground">mechanism:</span> AMM=
            {m.amm_trades} RFQ={m.rfq_trades} rfq_share={rfqShareTxt}
          </p>
          <p>
            <span className="text-muted-foreground">convergence:</span>{" "}
            {fmtPct(attribution.convergence_share)} (scored=
            {attribution.n_convergence_scored})
          </p>
          <p>
            <span className="text-muted-foreground">arb_bot:</span>{" "}
            {fmtPct(detail.arb_bot_trade_share)}{" "}
            <span className="text-muted-foreground">price_keeper:</span>{" "}
            {fmtPct(detail.price_keeper_trade_share)}
          </p>
        </div>
      </div>

      <div>
        <h3 className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          Top takers
        </h3>
        {top.length === 0 ? (
          <EmptyPanel message="No labeled takers in window." />
        ) : (
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full min-w-[520px] border-collapse text-xs">
              <thead>
                <tr className="border-b border-border bg-muted/40 text-muted-foreground">
                  <th className="px-2 py-1.5 text-left text-[10px] font-medium uppercase">
                    Address
                  </th>
                  <th className="px-2 py-1.5 text-left text-[10px] font-medium uppercase">
                    Label
                  </th>
                  <th className="px-2 py-1.5 text-right text-[10px] font-medium uppercase">
                    N
                  </th>
                  <th className="px-2 py-1.5 text-right text-[10px] font-medium uppercase">
                    Notional
                  </th>
                  <th className="px-2 py-1.5 text-right text-[10px] font-medium uppercase">
                    Conv
                  </th>
                  <th className="px-2 py-1.5 text-left text-[10px] font-medium uppercase">
                    Type
                  </th>
                </tr>
              </thead>
              <tbody>
                {top.map((t) => {
                  const f = t.features;
                  const kind =
                    f.is_contract === true
                      ? "C"
                      : f.is_contract === false
                        ? "E"
                        : "?";
                  return (
                    <tr
                      key={f.address}
                      className="border-b border-border/60 hover:bg-muted/30"
                    >
                      <td
                        className="px-2 py-1 font-mono text-[11px]"
                        title={f.address}
                      >
                        {shortAddr(f.address)}
                      </td>
                      <td className="px-2 py-1 text-[11px]">
                        {fmtLabel(t.label)}
                      </td>
                      <td className="px-2 py-1 text-right tabular-nums">
                        {f.n_trades}
                      </td>
                      <td className="px-2 py-1 text-right tabular-nums">
                        {fmtNotional(f.notional_usd)}
                      </td>
                      <td className="px-2 py-1 text-right tabular-nums">
                        {fmtPct(f.convergence_ratio)}
                      </td>
                      <td className="px-2 py-1 text-muted-foreground">{kind}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

/** Two-slice SVG donut for AMM vs RFQ counts (graceful when RFQ=0). */
function MechanismDonut({
  amm,
  rfq,
  rfqShareLabel,
}: {
  amm: number;
  rfq: number;
  rfqShareLabel: string;
}) {
  const total = amm + rfq;
  const size = 112;
  const stroke = 14;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;

  if (total === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-border p-3 text-[10px] text-muted-foreground">
        No fills
      </div>
    );
  }

  const ammFrac = amm / total;
  const rfqFrac = rfq / total;
  // When RFQ is unscoped (0), show full ring as AMM with muted label.
  const ammLen = c * ammFrac;
  const rfqLen = c * rfqFrac;

  return (
    <div className="flex flex-col items-center gap-1">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke="hsl(220 10% 18%)"
            strokeWidth={stroke}
          />
          {amm > 0 && (
            <circle
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke="hsl(142 55% 45%)"
              strokeWidth={stroke}
              strokeDasharray={`${ammLen} ${c - ammLen}`}
              strokeDashoffset={0}
            />
          )}
          {rfq > 0 && (
            <circle
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke="hsl(38 80% 55%)"
              strokeWidth={stroke}
              strokeDasharray={`${rfqLen} ${c - rfqLen}`}
              strokeDashoffset={-ammLen}
            />
          )}
        </g>
        <text
          x="50%"
          y="48%"
          textAnchor="middle"
          className="fill-foreground"
          style={{ fontSize: 11, fontFamily: "ui-monospace, Menlo, monospace" }}
        >
          {total}
        </text>
        <text
          x="50%"
          y="62%"
          textAnchor="middle"
          className="fill-muted-foreground"
          style={{ fontSize: 9, fontFamily: "ui-monospace, Menlo, monospace" }}
        >
          fills
        </text>
      </svg>
      <div className="flex flex-wrap justify-center gap-2 text-[10px] text-muted-foreground">
        <span className={cn("inline-flex items-center gap-1")}>
          <span className="h-1.5 w-1.5 rounded-full bg-positive" />
          AMM {amm}
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="h-1.5 w-1.5 rounded-full bg-warning" />
          RFQ {rfqShareLabel}
        </span>
      </div>
    </div>
  );
}
