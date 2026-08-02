"use client";

import { EmptyPanel } from "@/components/ui/empty-panel";
import { Badge } from "@/components/ui/badge";
import {
  fmtLabel,
  fmtNotional,
  fmtPct,
  fmtTsMs,
  shortAddr,
} from "@/lib/format";
import type {
  AddressPanelRow,
  PairAttribution,
  PairDetailResponse,
} from "@/lib/types";

type Props = {
  attribution: PairAttribution | null;
  addressPanel?: AddressPanelRow[] | null;
  detail: Pick<
    PairDetailResponse,
    "arb_bot_trade_share" | "price_keeper_trade_share" | "rfq_mechanism_share"
  >;
};

function labelVariant(
  label: string,
): "mm" | "warning" | "muted" | "default" | "positive" {
  if (label === "market_maker") return "mm";
  if (label === "arb_bot") return "warning";
  if (label === "price_keeper") return "positive";
  if (label === "rebalancer") return "warning";
  return "muted";
}

export function AttributionPanel({
  attribution,
  addressPanel,
  detail,
}: Props) {
  if (attribution == null && (!addressPanel || addressPanel.length === 0)) {
    return (
      <EmptyPanel message="No attribution yet — no AMM trades scored in the detail window." />
    );
  }

  const m = attribution?.mechanism ?? { amm_trades: 0, rfq_trades: 0 };
  // Pair-scoped RFQ fills are often 0 until enrichment (DEFERRED); avoid a
  // false "0% RFQ" ring — surface n/a when rfq_trades == 0. Do not invent a
  // share from counts when the API left rfq_mechanism_share null.
  const rfqShareTxt =
    m.rfq_trades === 0
      ? "n/a (fills unscoped)"
      : fmtPct(detail.rfq_mechanism_share);

  // Prefer extended address_panel (WHI-769); fall back to top_takers.
  const rows: AddressPanelRow[] =
    addressPanel && addressPanel.length > 0
      ? addressPanel
      : (attribution?.top_takers ?? []).map((t) => ({
          address: t.features.address,
          label: t.label,
          evidence_summary: null,
          is_rebalancer: false,
          n_trades: t.features.n_trades,
          notional_usd: t.features.notional_usd,
          convergence_ratio: t.features.convergence_ratio,
          last_active_ms: null,
          source: null,
          n_rfq_maker: 0,
          n_amm: t.features.n_trades,
        }));

  return (
    <div className="space-y-3">
      {attribution != null && (
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
      )}

      <div>
        <h3 className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          Top addresses
        </h3>
        {rows.length === 0 ? (
          <EmptyPanel message="No labeled addresses in window." />
        ) : (
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full min-w-[640px] border-collapse text-xs">
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
                    Last
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const highlight = r.label === "market_maker";
                  return (
                    <tr
                      key={r.address}
                      className={
                        highlight
                          ? "border-b border-border/60 bg-sky-500/5 hover:bg-sky-500/10"
                          : "border-b border-border/60 hover:bg-muted/30"
                      }
                    >
                      <td
                        className="px-2 py-1 font-mono text-[11px]"
                        title={r.address}
                      >
                        {shortAddr(r.address)}
                      </td>
                      <td className="px-2 py-1">
                        <span className="inline-flex flex-wrap items-center gap-1">
                          <Badge
                            variant={labelVariant(r.label)}
                            className="normal-case"
                            title={
                              r.evidence_summary
                                ? r.evidence_summary
                                : fmtLabel(r.label)
                            }
                          >
                            {fmtLabel(r.label)}
                          </Badge>
                          {r.is_rebalancer && (
                            <Badge
                              variant="warning"
                              className="normal-case"
                              title="CEX-touch rebalancer flag (orthogonal)"
                            >
                              reb
                            </Badge>
                          )}
                        </span>
                      </td>
                      <td className="px-2 py-1 text-right tabular-nums">
                        {r.n_trades}
                      </td>
                      <td className="px-2 py-1 text-right tabular-nums">
                        {fmtNotional(r.notional_usd)}
                      </td>
                      <td className="px-2 py-1 text-right tabular-nums">
                        {fmtPct(r.convergence_ratio)}
                      </td>
                      <td
                        className="px-2 py-1 text-muted-foreground tabular-nums"
                        title={
                          r.last_active_ms != null
                            ? new Date(r.last_active_ms).toISOString()
                            : undefined
                        }
                      >
                        {fmtTsMs(r.last_active_ms)}
                      </td>
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
  // Theme-aligned strokes (same hues as Tailwind positive / warning).
  const ammStroke = "hsl(142 55% 45%)";
  const rfqStroke = "hsl(38 80% 55%)";

  if (total === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-border p-3 text-[10px] text-muted-foreground">
        No fills
      </div>
    );
  }

  const ammFrac = amm / total;
  const rfqFrac = rfq / total;
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
              stroke={ammStroke}
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
              stroke={rfqStroke}
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
      <div className="flex flex-col items-center gap-0.5 text-[10px] text-muted-foreground">
        <span className="inline-flex items-center gap-1">
          <span className="h-1.5 w-1.5 rounded-full bg-positive" />
          AMM n={amm}
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="h-1.5 w-1.5 rounded-full bg-warning" />
          RFQ n={rfq} · share {rfqShareLabel}
        </span>
      </div>
    </div>
  );
}
