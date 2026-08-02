"use client";

/**
 * Detail-page premium panel (WHI-779): current CEX/AMM/RFQ vs underlying +
 * journal-window distributions. Explicit empty states for private / no_data.
 */

import { Badge } from "@/components/ui/badge";
import { EmptyPanel } from "@/components/ui/empty-panel";
import { cn } from "@/lib/cn";
import {
  bpsTone,
  fmtPrice,
  fmtSignedBps,
  priceTypeBadgeVariant,
} from "@/lib/format";
import type { Distribution, PremiumPanel as PremiumPanelModel } from "@/lib/types";

type Props = {
  premium: PremiumPanelModel | null | undefined;
  hasRfq?: boolean;
};

function DistRow({ label, d }: { label: string; d: Distribution }) {
  if (d.count === 0) {
    return (
      <tr className="text-muted-foreground">
        <td className="py-0.5 pr-3">{label}</td>
        <td className="py-0.5 text-right tabular-nums" colSpan={4}>
          n=0
        </td>
      </tr>
    );
  }
  return (
    <tr>
      <td className="py-0.5 pr-3 text-muted-foreground">{label}</td>
      <td className="py-0.5 text-right tabular-nums">{fmtSignedBps(d.p50)}</td>
      <td className="py-0.5 text-right tabular-nums">{fmtSignedBps(d.p95)}</td>
      <td className="py-0.5 text-right tabular-nums">{fmtSignedBps(d.p99)}</td>
      <td className="py-0.5 text-right tabular-nums">{fmtSignedBps(d.max)}</td>
    </tr>
  );
}

function PremValue({
  label,
  value,
}: {
  label: string;
  value: string | null | undefined;
}) {
  const tone = bpsTone(value ?? null);
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "tabular-nums",
          tone === "pos" && "text-positive",
          tone === "neg" && "text-negative",
          tone === "empty" && "text-muted-foreground",
        )}
      >
        {fmtSignedBps(value)}
      </dd>
    </div>
  );
}

export function PremiumPanelView({ premium, hasRfq = true }: Props) {
  if (!premium) {
    return (
      <EmptyPanel message="Premium not available — waiting for underlying poller." />
    );
  }
  const cur = premium.current;
  if (cur.empty_reason === "private") {
    return (
      <EmptyPanel
        message={`${cur.ticker} is private — no public equity feed (premium n/a).`}
      />
    );
  }
  if (cur.empty_reason === "no_data" || cur.price == null) {
    return (
      <EmptyPanel
        message={`No underlying print yet for ${cur.ticker} — collector poller may still be starting.`}
      />
    );
  }

  return (
    <div className="space-y-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-foreground">{cur.ticker}</span>
        <span className="tabular-nums text-foreground">
          {fmtPrice(cur.price)}
          {cur.currency ? ` ${cur.currency}` : ""}
        </span>
        {cur.price_type && (
          <Badge
            variant={priceTypeBadgeVariant(cur.price_type)}
            className="normal-case"
          >
            {cur.type_label ?? cur.price_type}
          </Badge>
        )}
        {cur.source && (
          <span className="text-[10px] text-muted-foreground">{cur.source}</span>
        )}
        {cur.as_of_ms != null && (
          <span className="text-[10px] text-muted-foreground">
            as_of {new Date(cur.as_of_ms).toLocaleString()}
          </span>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
        <PremValue label="CEX premium" value={cur.cex_premium_bps} />
        <PremValue label="AMM premium" value={cur.amm_premium_bps} />
        {hasRfq && <PremValue label="RFQ premium" value={cur.rfq_premium_bps} />}
      </dl>

      <div>
        <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          CEX premium distribution (journal window)
        </h3>
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-muted-foreground">
              <th className="py-0.5 pr-3 text-left font-medium">Session</th>
              <th className="py-0.5 text-right font-medium">P50</th>
              <th className="py-0.5 text-right font-medium">P95</th>
              <th className="py-0.5 text-right font-medium">P99</th>
              <th className="py-0.5 text-right font-medium">Max</th>
            </tr>
          </thead>
          <tbody>
            <DistRow label="All" d={premium.distribution} />
            <DistRow label="Open" d={premium.distribution_open} />
            <DistRow label="Closed" d={premium.distribution_closed} />
          </tbody>
        </table>
      </div>
    </div>
  );
}
