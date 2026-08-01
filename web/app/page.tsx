"use client";

/**
 * Skeleton acceptance page (WHI-757): poll /api/pairs and render a plain table.
 * No styling framework — WHI-758 lands the real overview UI.
 */

import { useCallback, useEffect, useState } from "react";

type PairRow = {
  pair_id: string;
  name: string;
  session: string | null;
  bybit_mid: string | null;
  amm_mid: string | null;
  rfq_buy: string | null;
  rfq_sell: string | null;
  amm_spread_bps: string | null;
  rfq_spread_bps: string | null;
  net_edge_bps: string | null;
  volume_24h: string;
  trades_24h: number;
  stale: boolean;
  low_liquidity: boolean;
};

type Overview = {
  generated_ts_ms: number;
  session_now: string;
  rows: PairRow[];
  db_path: string;
  error?: string | null;
};

type Health = {
  ok: boolean;
  collector_alive: boolean;
  last_block: number | null;
  age_ms: number | null;
  poll_interval_s: number | null;
  error?: string | null;
};

/** Same-origin relative URL works behind nginx; override for local dogfood. */
function apiBase(): string {
  if (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE) {
    return process.env.NEXT_PUBLIC_API_BASE.replace(/\/$/, "");
  }
  return "";
}

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`${path} → HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

function fmt(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  return String(v);
}

export default function HomePage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pollMs, setPollMs] = useState(2000);

  const refresh = useCallback(async () => {
    try {
      const [h, o] = await Promise.all([
        fetchJson<Health>("/api/health"),
        fetchJson<Overview>("/api/pairs"),
      ]);
      setHealth(h);
      setOverview(o);
      setErr(null);
      if (h.poll_interval_s && h.poll_interval_s > 0) {
        setPollMs(Math.round(h.poll_interval_s * 1000));
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => {
      void refresh();
    }, pollMs);
    return () => window.clearInterval(id);
  }, [refresh, pollMs]);

  return (
    <main style={{ padding: "1rem 1.25rem" }}>
      <h1 style={{ margin: "0 0 0.5rem", fontSize: "1.1rem" }}>
        xStocks monitor — skeleton (WHI-757)
      </h1>
      <p style={{ margin: "0 0 1rem", opacity: 0.75, fontSize: "0.85rem" }}>
        Polling /api/pairs every {pollMs}ms. Static export + FastAPI behind
        nginx. Real overview UI lands in WHI-758.
      </p>

      {err && (
        <div
          style={{
            marginBottom: "1rem",
            padding: "0.5rem 0.75rem",
            background: "#3d1f1f",
            border: "1px solid #a33",
          }}
        >
          fetch error: {err}
        </div>
      )}

      {health && (
        <div
          style={{
            marginBottom: "1rem",
            padding: "0.5rem 0.75rem",
            background: health.ok ? "#13261a" : "#3d2a12",
            border: `1px solid ${health.ok ? "#2a6" : "#a80"}`,
            fontSize: "0.85rem",
          }}
        >
          health: {health.ok ? "ok" : "degraded"} · collector_alive=
          {String(health.collector_alive)} · last_block={fmt(health.last_block)}{" "}
          · age_ms={fmt(health.age_ms)}
          {health.error ? ` · ${health.error}` : ""}
        </div>
      )}

      {overview && (
        <>
          <p style={{ fontSize: "0.8rem", opacity: 0.7 }}>
            session={overview.session_now} · generated_ts_ms=
            {overview.generated_ts_ms} · db={overview.db_path} · n=
            {overview.rows.length}
          </p>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "0.8rem",
            }}
          >
            <thead>
              <tr>
                {[
                  "pair",
                  "sess",
                  "bybit mid",
                  "amm mid",
                  "rfq buy",
                  "rfq sell",
                  "amm bps",
                  "rfq bps",
                  "net bps",
                  "vol 24h",
                  "trades",
                  "flags",
                ].map((h) => (
                  <th
                    key={h}
                    style={{
                      textAlign: "left",
                      borderBottom: "1px solid #333",
                      padding: "0.35rem 0.4rem",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {overview.rows.map((r) => (
                <tr
                  key={r.pair_id}
                  style={{
                    opacity: r.low_liquidity || r.stale ? 0.55 : 1,
                  }}
                >
                  <td style={{ padding: "0.3rem 0.4rem" }}>{r.pair_id}</td>
                  <td style={{ padding: "0.3rem 0.4rem" }}>{fmt(r.session)}</td>
                  <td style={{ padding: "0.3rem 0.4rem" }}>
                    {fmt(r.bybit_mid)}
                  </td>
                  <td style={{ padding: "0.3rem 0.4rem" }}>{fmt(r.amm_mid)}</td>
                  <td style={{ padding: "0.3rem 0.4rem" }}>{fmt(r.rfq_buy)}</td>
                  <td style={{ padding: "0.3rem 0.4rem" }}>
                    {fmt(r.rfq_sell)}
                  </td>
                  <td style={{ padding: "0.3rem 0.4rem" }}>
                    {fmt(r.amm_spread_bps)}
                  </td>
                  <td style={{ padding: "0.3rem 0.4rem" }}>
                    {fmt(r.rfq_spread_bps)}
                  </td>
                  <td style={{ padding: "0.3rem 0.4rem" }}>
                    {fmt(r.net_edge_bps)}
                  </td>
                  <td style={{ padding: "0.3rem 0.4rem" }}>
                    {fmt(r.volume_24h)}
                  </td>
                  <td style={{ padding: "0.3rem 0.4rem" }}>{r.trades_24h}</td>
                  <td style={{ padding: "0.3rem 0.4rem" }}>
                    {[
                      r.stale ? "stale" : null,
                      r.low_liquidity ? "low-liq" : null,
                    ]
                      .filter(Boolean)
                      .join(" ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </main>
  );
}
