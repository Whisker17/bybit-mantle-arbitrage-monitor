"use client";

import {
  bpsTone,
  fmtBps,
  fmtDirection,
  fmtSignedBps,
  parseNum,
  totalWearBps,
} from "@/lib/format";
import type { CostBreakdown, Distribution, EdgePanel } from "@/lib/types";
import { cn } from "@/lib/cn";

type Props = {
  amm: EdgePanel;
  rfq: EdgePanel;
  /** Sample cap stated in the TUI header (display only). */
  sampleCapHint?: string;
};

export function EdgeStatsPanel({ amm, rfq, sampleCapHint }: Props) {
  return (
    <div className="space-y-3">
      <p className="text-[10px] text-muted-foreground">
        Net paper edge (M3) — cumulative over last journal book samples
        {sampleCapHint ? ` (≤${sampleCapHint})` : ""}. Bucket / optimal size PnL
        awaits WHI-756.
      </p>
      <div className="grid gap-3 lg:grid-cols-2">
        <VenueEdgeCard title="AMM" panel={amm} />
        <VenueEdgeCard title="RFQ" panel={rfq} note="unslipped ladder (see DEFERRED)" />
      </div>
      <BucketPlaceholder />
    </div>
  );
}

function VenueEdgeCard({
  title,
  panel,
  note,
}: {
  title: string;
  panel: EdgePanel;
  note?: string;
}) {
  const cur = panel.current;
  const costs = cur?.costs ?? panel.costs;

  return (
    <div className="rounded-md border border-border bg-card px-3 py-2.5 space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold">{title}</h3>
        {note && (
          <span className="text-[10px] text-muted-foreground">{note}</span>
        )}
      </div>

      {cur == null ? (
        <p className="text-[11px] text-muted-foreground">No fillable edge</p>
      ) : (
        <div className="space-y-1 text-xs">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <Bps value={cur.net_edge_bps} className="text-sm font-semibold" />
            <span className="text-muted-foreground">
              {fmtDirection(cur.direction)} @ ${Number(cur.size_usd).toLocaleString()}
            </span>
            <span className="text-muted-foreground">
              gross {fmtSignedBps(cur.gross_spread_bps)}
            </span>
          </div>
        </div>
      )}

      <CostWaterfall costs={costs} />

      <div className="space-y-0.5 border-t border-border/60 pt-2 text-[11px]">
        <DistRow label="all" dist={panel.distribution_all} />
        <DistRow label="open" dist={panel.distribution_open} />
        <DistRow label="closed" dist={panel.distribution_closed} />
        <p className="text-muted-foreground pt-0.5">
          breaches: episodes={panel.breach_all.episode_count}{" "}
          dur_ms={panel.breach_all.total_duration_ms}{" "}
          now={panel.breach_all.currently_breaching ? "Y" : "N"}
        </p>
      </div>
    </div>
  );
}

function DistRow({ label, dist }: { label: string; dist: Distribution }) {
  if (dist.count === 0) {
    return (
      <p className="tabular-nums text-muted-foreground">
        {label}: n=0
      </p>
    );
  }
  return (
    <p className="tabular-nums">
      <span className="text-muted-foreground">{label}:</span> n={dist.count}{" "}
      p50={fmtBps(dist.p50)} p95={fmtBps(dist.p95)} p99={fmtBps(dist.p99)}{" "}
      max={fmtBps(dist.max)}
    </p>
  );
}

const WEAR_ROWS: Array<{
  key: keyof CostBreakdown;
  label: string;
}> = [
  { key: "bybit_taker_bps", label: "Bybit fee" },
  { key: "fluxion_fee_bps", label: "Fluxion fee" },
  { key: "bybit_slip_bps", label: "Bybit slip" },
  { key: "fluxion_slip_bps", label: "Fluxion slip" },
  { key: "gas_bps", label: "Gas" },
  { key: "basis_bps", label: "USDT/USDC basis" },
];

function CostWaterfall({ costs }: { costs: CostBreakdown | null }) {
  if (!costs) {
    return (
      <p className="text-[10px] text-muted-foreground">No cost breakdown</p>
    );
  }
  const total = totalWearBps(costs);
  const totalN = parseNum(total) ?? 0;
  const maxBar = Math.max(
    totalN,
    ...WEAR_ROWS.map((r) => Math.abs(parseNum(costs[r.key] as string) ?? 0)),
    1,
  );

  return (
    <div className="space-y-1">
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        Cost waterfall (bps)
      </p>
      {WEAR_ROWS.map((r) => {
        const v = parseNum(costs[r.key] as string) ?? 0;
        const pct = Math.min(100, (Math.abs(v) / maxBar) * 100);
        return (
          <div key={r.key} className="grid grid-cols-[7.5rem_1fr_2.5rem] items-center gap-1.5 text-[10px]">
            <span className="text-muted-foreground truncate">{r.label}</span>
            <div className="h-1.5 rounded-sm bg-muted overflow-hidden">
              <div
                className="h-full rounded-sm bg-warning/70"
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="tabular-nums text-right">{fmtBps(v)}</span>
          </div>
        );
      })}
      <div className="grid grid-cols-[7.5rem_1fr_2.5rem] items-center gap-1.5 text-[10px] font-medium">
        <span>Total wear</span>
        <div />
        <span className="tabular-nums text-right">{fmtBps(total)}</span>
      </div>
    </div>
  );
}

function Bps({
  value,
  className,
}: {
  value: string | null;
  className?: string;
}) {
  const tone = bpsTone(value);
  return (
    <span
      className={cn(
        "tabular-nums",
        tone === "pos" && "text-positive",
        tone === "neg" && "text-negative",
        tone === "empty" && "text-muted-foreground",
        className,
      )}
    >
      {fmtSignedBps(value)} bps
    </span>
  );
}

function BucketPlaceholder() {
  return (
    <div className="rounded-md border border-dashed border-border px-3 py-2.5">
      <p className="text-xs font-semibold mb-1">Bucket PnL / optimal size</p>
      <p className="text-[11px] text-muted-foreground">
        Optimal notional search and the USD bucket table ($10 / $50 / $100 /
        $500 / $1K / $10K) land with{" "}
        <strong className="text-foreground">WHI-756</strong>. Depth curve is
        already collected (WHI-755); this panel will fill once the engine
        ships.
      </p>
    </div>
  );
}
