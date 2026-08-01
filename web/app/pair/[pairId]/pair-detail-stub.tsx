"use client";

/**
 * Minimal pair landing so overview row clicks resolve under static export.
 * Full detail (spread series, trades, attribution) is WHI-759.
 */

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { fetchJson } from "@/lib/api";
import {
  fmtDirection,
  fmtNotional,
  fmtPrice,
  fmtSession,
  fmtSignedBps,
} from "@/lib/format";
import type { PairDetailStubResponse, SessionKind } from "@/lib/types";

export function PairDetailStub({ pairId }: { pairId: string }) {
  const [data, setData] = useState<PairDetailStubResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const d = await fetchJson<PairDetailStubResponse>(
          `/api/pairs/${encodeURIComponent(pairId)}`,
        );
        if (!cancelled) {
          setData(d);
          setErr(null);
        }
      } catch (e) {
        if (!cancelled) {
          setErr(e instanceof Error ? e.message : String(e));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pairId]);

  if (err) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive-foreground">
        {err}
      </div>
    );
  }

  if (!data) {
    return (
      <p className="text-xs text-muted-foreground">Loading {pairId}…</p>
    );
  }

  const o = data.overview;
  const session: SessionKind = data.session_now;

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-border bg-card px-3 py-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold">{data.name}</span>
          <Badge variant={session === "open" ? "open" : "closed"}>
            {fmtSession(session)}
          </Badge>
          {data.low_liquidity && <Badge variant="muted">low-liq</Badge>}
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-3">
          <Field label="Bybit mid" value={fmtPrice(o.bybit_mid)} />
          <Field label="AMM mid" value={fmtPrice(o.amm_mid)} />
          <Field
            label="RFQ"
            value={`${fmtPrice(o.rfq_buy)} / ${fmtPrice(o.rfq_sell)}`}
          />
          <Field label="AMM bps" value={fmtSignedBps(o.amm_spread_bps)} />
          <Field label="RFQ bps" value={fmtSignedBps(o.rfq_spread_bps)} />
          <Field label="Net edge" value={fmtSignedBps(o.net_edge_bps)} />
          <Field label="Direction" value={fmtDirection(o.net_edge_direction)} />
          <Field label="Venue" value={o.net_edge_venue ?? "—"} />
          <Field
            label="Vol / trades 24h"
            value={`${fmtNotional(o.volume_24h)} / ${o.trades_24h}`}
          />
        </dl>
      </div>

      <p className="rounded-md border border-dashed border-border px-3 py-2 text-[11px] text-muted-foreground">
        Full pair detail (spread history, trade stream, edge stats, attribution)
        lands in <strong className="text-foreground">WHI-759</strong>. This
        route exists so overview row clicks resolve under the static export.
      </p>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="tabular-nums text-foreground">{value}</dd>
    </div>
  );
}
