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

/** PnL v2 overview / detail status from monitor.metrics.pnl_snapshot. */
export type PnlStatus =
  | "ok"
  | "no_book"
  | "no_pool"
  | "no_depth"
  | "no_fillable"
  | "stale";

export type PnlDepthSource = "l1" | "book";

/** Compact optimal-size card on each overview row (WHI-766). */
export type PnlOptimalSummary = {
  status: PnlStatus;
  has_depth: boolean;
  direction: Direction | null;
  optimal_notional_usd: string | null;
  optimal_net_pnl_usd: string | null;
  optimal_net_pnl_bps: string | null;
  bybit_depth_source: PnlDepthSource | null;
};

export type PnlCostBreakdownUsd = {
  bybit_fee_usd: string;
  bybit_slip_usd: string;
  fluxion_fee_usd: string;
  fluxion_slip_usd: string;
  gas_usd: string;
  basis_usd: string;
};

export type PnlResult = {
  pair_id: string;
  venue: VenueKind;
  direction: Direction;
  size_usd: string;
  q_base: string;
  bybit_mid: string;
  spent_usd: string;
  recv_usd: string;
  pnl_usd: string;
  pnl_bps: string | null;
  fillable: boolean;
  reason?: string | null;
  bybit_depth_source: PnlDepthSource;
  meets_min_profit: boolean;
  costs: PnlCostBreakdownUsd;
};

export type OptimalSizeResult = {
  pair_id: string;
  direction: Direction;
  q_star_usd: string;
  pnl_usd: string;
  q_min_usd: string;
  q_max_usd: string;
  depth_cap_usd: string | null;
  amm_cap_usd: string;
  samples_evaluated: number;
  result: PnlResult;
};

export type PnlBucketTable = {
  pair_id: string;
  direction: Direction;
  amm_buckets: PnlResult[];
  rfq_rows: PnlResult[];
  optimal: OptimalSizeResult | null;
};

/** Full dual-direction snapshot on pair detail (WHI-766). */
export type PnlPairSnapshot = {
  status: PnlStatus;
  has_depth: boolean;
  best: PnlOptimalSummary;
  tables: Partial<Record<Direction, PnlBucketTable>>;
};

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
  /** Present after WHI-766; older APIs may omit. */
  pnl_v2?: PnlOptimalSummary | null;
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

/** Full pair detail model from GET /api/pairs/{id} (TUI PairDetailModel + PnL v2). */
export type PairDetailResponse = {
  pair_id: string;
  name: string;
  low_liquidity: boolean;
  generated_ts_ms: number;
  session_now: SessionKind;
  overview: PairOverviewRow;
  spread_series: SpreadPoint[];
  trades: TradeStreamRow[];
  edge_amm: EdgePanel;
  edge_rfq: EdgePanel;
  attribution: PairAttribution | null;
  arb_bot_trade_share: number | null;
  price_keeper_trade_share: number | null;
  rfq_mechanism_share: number | null;
  /** Dual-direction bucket tables + optimal (WHI-766). */
  pnl_v2?: PnlPairSnapshot | null;
  error?: string | null;
};

export type SpreadPoint = {
  ts_ms: number;
  amm_spread_bps: string | null;
  session: SessionKind;
  rfq_spread_bps?: string | null;
  bybit_mid?: string | null;
};

export type TradeStreamRow = {
  ts_ms: number;
  mechanism: string;
  direction: string;
  notional_usd: string | null;
  price: string | null;
  bybit_mid: string | null;
  converging: boolean | null;
  taker: string | null;
  taker_label: string | null;
  tx_hash: string;
};

export type CostBreakdown = {
  bybit_taker_bps: string;
  fluxion_fee_bps: string;
  bybit_slip_bps: string;
  fluxion_slip_bps: string;
  gas_bps: string;
  basis_bps: string;
};

export type EdgeResult = {
  pair_id: string;
  venue: VenueKind;
  direction: Direction;
  size_usd: string;
  bybit_mid: string;
  fluxion_mid: string;
  gross_spread_bps: string;
  costs: CostBreakdown;
  net_edge_bps: string;
  fillable: boolean;
  reason?: string | null;
};

export type Distribution = {
  count: number;
  p50: string | null;
  p95: string | null;
  p99: string | null;
  max: string | null;
};

export type BreachStats = {
  episode_count: number;
  total_duration_ms: number;
  currently_breaching: boolean;
};

export type EdgePanel = {
  current: EdgeResult | null;
  distribution_all: Distribution;
  distribution_open: Distribution;
  distribution_closed: Distribution;
  breach_all: BreachStats;
  breach_open: BreachStats;
  breach_closed: BreachStats;
  costs: CostBreakdown | null;
};

export type BehaviorLabel =
  | "arb_bot"
  | "price_keeper"
  | "retail"
  | "unknown";

export type AddressFeatures = {
  address: string;
  n_trades: number;
  n_buy: number;
  n_sell: number;
  notional_usd: string;
  median_notional_usd: string;
  max_notional_usd: string;
  open_share: number;
  closed_share: number;
  activity_regime: string;
  trades_per_day: number | null;
  convergence_ratio: number | null;
  n_convergence_scored: number;
  bybit_align_ratio: number | null;
  n_bybit_align_scored: number;
  is_contract: boolean | null;
  role: string | null;
};

export type TakerProfile = {
  features: AddressFeatures;
  label: BehaviorLabel;
};

export type MechanismShare = {
  amm_trades: number;
  rfq_trades: number;
};

export type PairAttribution = {
  pair_id: string;
  session: string;
  window_start_ms: number | null;
  window_end_ms: number | null;
  mechanism: MechanismShare;
  convergence_share: number | null;
  n_convergence_scored: number;
  label_trade_share: Record<string, number>;
  label_trade_counts: Record<string, number>;
  top_takers: TakerProfile[];
  takers: TakerProfile[];
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
