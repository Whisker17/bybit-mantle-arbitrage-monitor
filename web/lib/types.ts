/**
 * Wire types for monitor.api JSON (Decimals → string; enums → value).
 * Keep in lockstep with monitor.tui.model + monitor.api.health.
 */

export type SessionKind = "open" | "closed";

/** M3 direction keys from monitor.metrics.edge.Direction. */
export type Direction =
  | "buy_fluxion_sell_bybit"
  | "buy_bybit_sell_fluxion";

export type VenueKind = "amm" | "rfq";

export type PairOverviewRow = {
  pair_id: string;
  name: string;
  low_liquidity: boolean;
  session: SessionKind | null;
  bybit_bid: string | null;
  bybit_ask: string | null;
  bybit_mid: string | null;
  amm_mid: string | null;
  rfq_buy: string | null;
  rfq_sell: string | null;
  amm_spread_bps: string | null;
  rfq_spread_bps: string | null;
  net_edge_bps: string | null;
  net_edge_venue: VenueKind | null;
  net_edge_direction: Direction | null;
  reference_size_usd: string;
  volume_24h: string;
  trades_24h: number;
  stale: boolean;
};

export type OverviewResponse = {
  generated_ts_ms: number;
  session_now: SessionKind;
  sort_key: string;
  sort_desc: boolean;
  reference_size_usd: string;
  rows: PairOverviewRow[];
  db_path: string;
  error?: string | null;
};

/** Subset of PairDetailModel used by the WHI-758 route stub (full detail = WHI-759). */
export type PairDetailStubResponse = {
  pair_id: string;
  name: string;
  low_liquidity: boolean;
  generated_ts_ms: number;
  session_now: SessionKind;
  overview: PairOverviewRow;
  error?: string | null;
};

export type CollectorGap = {
  gap_id?: string | null;
  source?: string | null;
  pair_id?: string | null;
  gap_start_ms?: number | null;
  gap_end_ms?: number | null;
  reason?: string | null;
  [key: string]: unknown;
};

export type HealthResponse = {
  ok: boolean;
  generated_ts_ms: number;
  db_path: string;
  db_exists: boolean;
  collector_started_ms: number | null;
  collector_stopped_ms: number | null;
  last_block: number | null;
  last_block_ingest_latency_ms: number | null;
  freshest_recv_ts_ms: number | null;
  age_ms: number | null;
  collector_alive: boolean;
  gap_recent: boolean;
  recent_gaps: CollectorGap[];
  poll_interval_s: number | null;
  error?: string | null;
};

/** Client-side sort keys (subset of TUI SortKey + pair name). */
export type SortKey =
  | "pair_id"
  | "net_edge"
  | "amm_spread"
  | "rfq_spread"
  | "bybit_mid"
  | "volume_24h"
  | "trades_24h";
