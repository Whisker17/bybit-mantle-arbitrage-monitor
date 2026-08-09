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
  | "empty_pool"
  | "invalid_mid"
  | "pricing_anomaly"
  | "no_depth"
  | "no_fillable"
  | "stale";

/**
 * AMM quote annotation (WHI-795 / WHI-822).
 * - empty_pool / invalid_mid: mid is null
 * - pricing_anomaly: mid may still be set (extreme |vs CEX|; not tradable)
 */
export type AmmQuoteReason = "empty_pool" | "invalid_mid" | "pricing_anomaly";

/**
 * Why a row is denied a Top-N seat (WHI-796 + WHI-822). UI maps via format
 * helpers; eligibility lives in web/lib/sort.ts.
 */
export type DexNonTradeableReason =
  | "empty_pool"
  | "invalid_mid"
  | "pricing_anomaly"
  | "no_pool"
  | "low_liq"
  | "no_quote";

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
  /**
   * WHI-962: true when optimal PnL clears configured min_profit floors
   * (min_profit_usd / min_profit_bps). Used with clears_drift for green.
   */
  meets_min_profit?: boolean;
  /** WHI-821: true when any leg exceeds quote_max_age_ms (quiet ≠ dead). */
  quote_aged?: boolean;
  cex_quote_age_ms?: number | null;
  amm_quote_age_ms?: number | null;
  depth_quote_age_ms?: number | null;
};

/**
 * The optimal fields the *overview* carries, as they arrive on the wire:
 * present-or-absent, not required (WHI-973).
 *
 * `PnlOptimalSummary` is the full detail-route shape; overview rows ship a
 * subset, so a bare `Pick<>` — which keeps required-ness — does not describe
 * them. Both the row types and the predicates that read them must name this
 * one type, or they drift apart again and the drift only surfaces as a
 * `next build` failure.
 */
export type PnlOptimalFloorFields = Partial<
  Pick<PnlOptimalSummary, "meets_min_profit" | "optimal_net_pnl_usd">
>;

/**
 * Sequential-execution drift bar (WHI-962).
 * ``drift_premium_bps = k × σ_session``; clears when net ≥ premium.
 */
export type DriftAnnotation = {
  sigma_transit_bps: string | null;
  drift_premium_k: string;
  drift_premium_bps: string | null;
  session: SessionKind | null;
  clears_drift: Partial<Record<Direction, boolean | null>>;
  /** Gate for overview optimal / Net direction. */
  clears_drift_optimal: boolean | null;
};

export type WithdrawalFeeKind = "stable" | "asset" | "unknown";

export type PnlCostBreakdownUsd = {
  bybit_fee_usd: string;
  bybit_slip_usd: string;
  fluxion_fee_usd: string;
  fluxion_slip_usd: string;
  gas_usd: string;
  basis_usd: string;
  withdrawal_fee_usd: string;
  /** WHI-961: which schedule produced withdrawal_fee_usd. ``unknown`` = unmeasured. */
  withdrawal_fee_kind: WithdrawalFeeKind;
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
  /** WHI-821: per-leg recv ages; null when API omitted now_ms. */
  cex_quote_age_ms?: number | null;
  amm_quote_age_ms?: number | null;
  depth_quote_age_ms?: number | null;
  quote_aged?: boolean;
  /** WHI-962: sequential bar nested on detail payload. */
  drift?: DriftAnnotation | null;
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
  /**
   * WHI-966 / ADR-0002: Q* notional that Net is evaluated at (same as
   * pnl_v2.optimal_notional_usd when status==ok). Null when Net is blank.
   * Distinct from reference_size_usd (M3 EdgeStats / detail ladder).
   * Always present after WHI-966 API enrichment (null when no Q*).
   */
  net_size_usd: string | null;
  reference_size_usd: string;
  volume_24h: string;
  trades_24h: number;
  stale: boolean;
  /**
   * WHI-795: why AMM mid / vs CEX / AMM vs Und are n/a when a pool tick
   * existed (empty_pool residual slot0, invalid_mid). Null when mid is usable
   * or there is no pool tick at all.
   */
  amm_quote_reason?: AmmQuoteReason | null;
  /** Present after WHI-766; older APIs may omit. */
  pnl_v2?: PnlOptimalSummary | null;
  /** Present after WHI-769; older APIs may omit. */
  mm_active?: MmActiveStatus | null;
  /** WHI-777: CEX REST 24h quote volume (null until first poll). */
  cex_volume_24h?: string | null;
  /** WHI-777: DEX AMM swap notional in window. */
  dex_volume_24h?: string | null;
  /** WHI-777: cex / dex when both positive. */
  volume_ratio?: string | null;
  dex_trade_count_24h?: number | null;
  cex_trade_count_24h?: number | null;
  dex_volume_truncated?: boolean;
  dex_volume_window_start_ms?: number | null;
  /**
   * WHI-974: why CEX Vol / volume_ratio are blank. `geo_blocked` when the
   * exchange REST path is blocked from this host (WS unaffected). Journal
   * derived volume stays on the pair detail panel only.
   */
  cex_volume_reason?: "geo_blocked" | null;
  /** WHI-779: underlying equity reference + tokenized premium. */
  underlying_ticker?: string | null;
  underlying_price?: string | null;
  underlying_currency?: string | null;
  underlying_price_type?: "live" | "pre" | "post" | "close" | "stale" | null;
  underlying_as_of_ms?: number | null;
  underlying_source?: string | null;
  /** Explicit empty: no_data | private (never a dashed placeholder alone). */
  underlying_empty?: "no_data" | "private" | null;
  /**
   * Legacy alias for CEX mid vs underlying (bps); same as cex_premium_bps.
   * Overview "vs Und" under CEX prefers cex_premium_bps (WHI-783).
   */
  premium_bps?: string | null;
  cex_premium_bps?: string | null;
  amm_premium_bps?: string | null;
  rfq_premium_bps?: string | null;
  premium_type_label?: string | null;
  /**
   * Inventory snapshot pool liquidity USD from TUI `PairOverviewRow`
   * (serialized by the API). Web overview ranks on live `tvl_usd` only
   * (WHI-791); this field is unused in the Web UI.
   */
  est_liquidity_usd?: string | null;
  /**
   * WHI-782: live DEX pool TVL (balanceOf × AMM mid). Capital size, not depth.
   * Null until the collector's first throttled TVL sample.
   */
  tvl_usd?: string | null;
  /** WHI-782: recv_ts_ms of the TVL sample used for tvl_usd. */
  tvl_as_of_ms?: number | null;
  /**
   * WHI-824: flat optimal PnL for sort keys. Set only when pnl_v2.status is
   * ``ok`` (including quote_aged); null for non-numeric statuses so sort parks
   * them last and Top-N skips them. Prefer these over digging into pnl_v2.
   */
  pnl_optimal_net_usd?: string | null;
  pnl_optimal_net_bps?: string | null;
  /**
   * WHI-962: sequential-execution bar. Session-selected σ, k×σ premium,
   * per-direction clears_drift map + clears_drift_optimal for Net/Bucket.
   */
  sigma_transit_bps?: string | null;
  drift_premium_k?: string | null;
  drift_premium_bps?: string | null;
  clears_drift?: Partial<Record<Direction, boolean | null>> | null;
  clears_drift_optimal?: boolean | null;
  /**
   * WHI-963: occupancy-bounded capture rate (compact overview card).
   * windows/day + capturable $/day under single-flight + re-entry cooldown.
   */
  capture?: CaptureOverview | null;
};

/** Capture status from monitor.metrics.capture.CaptureStatus. */
export type CaptureStatus =
  | "ok"
  | "disabled"
  | "insufficient"
  | "no_samples"
  | "no_pool";

/** Compact capture card on overview rows (WHI-963). */
export type CaptureOverview = {
  status: CaptureStatus;
  windows_per_day: number | null;
  capturable_usd_per_day: string | null;
  n_windows: number | null;
  direction: Direction | null;
  session: SessionKind | null;
  venue: VenueKind | null;
  span_ms: number;
  lookback_ms: number;
  size_usd: string;
};

export type CaptureSeries = {
  pair_id: string;
  direction: Direction;
  session: SessionKind;
  venue: VenueKind;
  size_usd: string;
  n_samples: number;
  n_windows: number;
  windows_per_day: number;
  capturable_usd: string;
  capturable_usd_per_day: string;
  span_ms: number;
};

export type CaptureSparkPoint = {
  bucket_start_ms: number;
  n_windows: number;
  capturable_usd: string;
};

/** Full capture snapshot on pair detail (WHI-963). */
export type CapturePairSnapshot = CaptureOverview & {
  pair_id: string;
  since_ms: number;
  until_ms: number;
  trade_duration_ms: number;
  reentry_cooldown_ms: number;
  series: CaptureSeries[];
  sparkline: CaptureSparkPoint[];
};

/** Session bucket for volume compare (WHI-777). */
export type SessionVolumeSlice = {
  volume_usd: string;
  trade_count: number;
};

export type DexVolumeWindow = {
  volume_usd: string;
  trade_count: number;
  open: SessionVolumeSlice;
  closed: SessionVolumeSlice;
  window_start_ms: number;
  requested_since_ms: number;
  now_ms: number;
  truncated: boolean;
  earliest_recv_ts_ms: number | null;
};

export type CexJournalVolumeWindow = {
  volume_usd: string;
  trade_count: number;
  open: SessionVolumeSlice;
  closed: SessionVolumeSlice;
  window_start_ms: number;
  truncated: boolean;
};

/** Detail mini-panel payload (PairDetailModel.volume_compare). */
export type VolumeCompare = {
  cex_volume_24h: string | null;
  cex_trade_count_24h: number | null;
  cex_source: string | null;
  cex_poll_ts_ms: number | null;
  dex: DexVolumeWindow;
  cex_journal: CexJournalVolumeWindow | null;
  volume_ratio: string | null;
  /** WHI-974: same vocabulary as overview row. */
  cex_volume_reason?: "geo_blocked" | null;
};

/** Market journal readiness for overview / markets list (WHI-774). */
export type MarketDataStatus = "ok" | "accumulating";

export type OverviewResponse = {
  generated_ts_ms: number;
  session_now: SessionKind;
  sort_key: string;
  sort_desc: boolean;
  reference_size_usd: string;
  rows: PairOverviewRow[];
  db_path: string;
  error?: string | null;
  /** Present after WHI-774 multi-market routes. */
  market_id?: string;
  display_name?: string;
  has_rfq?: boolean;
  data_status?: MarketDataStatus;
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
  /** Extended address table (labels + evidence) — WHI-769. */
  address_panel?: AddressPanelRow[] | null;
  /** WHI-777: CEX vs DEX 24h volume compare with session splits. */
  volume_compare?: VolumeCompare | null;
  /** WHI-779: current premium + journal-window distributions. */
  premium?: PremiumPanel | null;
  /** WHI-963: occupancy-bounded capture rate + windows/day sparkline. */
  capture?: CapturePairSnapshot | null;
  error?: string | null;
  /** Present after WHI-774 multi-market routes. */
  market_id?: string;
  display_name?: string;
  has_rfq?: boolean;
  data_status?: MarketDataStatus;
};

export type PremiumSnapshot = {
  ticker: string;
  price: string | null;
  currency: string | null;
  price_type: "live" | "pre" | "post" | "close" | "stale" | null;
  as_of_ms: number | null;
  source: string | null;
  premium_bps: string | null;
  cex_premium_bps: string | null;
  amm_premium_bps: string | null;
  rfq_premium_bps: string | null;
  type_label: string | null;
  empty_reason: "no_data" | "private" | null;
};

export type PremiumPanel = {
  current: PremiumSnapshot;
  distribution: Distribution;
  distribution_open: Distribution;
  distribution_closed: Distribution;
};

export type SpreadPoint = {
  ts_ms: number;
  /** DEX AMM mid vs CEX mid (bps). UI label: vs CEX. */
  amm_spread_bps: string | null;
  session: SessionKind;
  /** RFQ mid vs CEX mid (bps). UI label: RFQ vs CEX. */
  rfq_spread_bps?: string | null;
  bybit_mid?: string | null;
  /** CEX equity-eq mid vs underlying (bps). UI: CEX vs Und. */
  cex_premium_bps?: string | null;
  /** AMM equity-eq mid vs underlying (bps). UI: DEX vs Und. */
  amm_premium_bps?: string | null;
  /**
   * RFQ mid vs underlying (bps) at this join tick. Serialized on the detail
   * series for API consumers; Web chart does not plot it (overview hover
   * uses the pair row's rfq_premium_bps). WHI-783.
   */
  rfq_premium_bps?: string | null;
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
  withdrawal_fee_bps: string;
  withdrawal_fee_usd: string;
  withdrawal_fee_kind: WithdrawalFeeKind;
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
  | "market_maker"
  | "arb_bot"
  | "rebalancer"
  | "price_keeper"
  | "retail"
  | "unknown";

/** Overview / detail MM activity badge (WHI-769). */
export type MmActiveStatus = "active" | "inactive" | "unknown";

/** GET /api/pairs/{id}/mm empty-state machine. */
export type MmDataStatus = "ok" | "accumulating" | "no_candidates";

/** Extended top-address row on pair detail (labels + evidence). */
export type AddressPanelRow = {
  address: string;
  label: string;
  evidence_summary: string | null;
  is_rebalancer: boolean;
  n_trades: number;
  notional_usd: string;
  convergence_ratio: number | null;
  last_active_ms: number | null;
  source: string | null;
  n_rfq_maker: number;
  n_amm: number;
  is_contract?: boolean | null;
};

export type InventoryPoint = {
  ts_ms: number;
  inventory: string;
  tx_hash: string;
  kind: string;
  delta_native: string;
};

export type MmAddressSeries = {
  address: string;
  label: string;
  evidence_summary: string | null;
  is_rebalancer: boolean;
  final_inventory: string;
  series: InventoryPoint[];
};

export type RebalanceTimelineItem = {
  address: string;
  counterparty: string;
  pair_id: string;
  token: string;
  amount: string;
  direction: string;
  block_number: number;
  block_ts: number;
  recv_ts_ms: number;
  tx_hash: string;
  log_index: number;
};

export type MmPairResponse = {
  pair_id: string;
  status: MmDataStatus;
  generated_ts_ms: number;
  addresses: MmAddressSeries[];
  rebalance_events: RebalanceTimelineItem[];
};

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

/** WHI-825 three-state feed vocabulary (+ ok). */
export type FeedState = "ok" | "feed_down" | "feed_quiet" | "gap";

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
  /** Present after WHI-774 multi-market routes. */
  market_id?: string;
  display_name?: string;
  has_rfq?: boolean;
  data_status?: MarketDataStatus;
  /** WHI-825: ok | feed_down | feed_quiet | gap */
  feed_state?: FeedState;
  /** Actionable recovery one-liner (which market, how long, how to restart). */
  recovery_hint?: string | null;
  heartbeat_age_ms?: number | null;
  collector_down_gap_recent?: boolean;
  /** WHI-974: RFQ poll error rate over rfq_coverage_window_ms. */
  rfq_error_rate?: number | null;
  rfq_error_rows?: number | null;
  rfq_total_rows?: number | null;
  rfq_availability_among_reachable?: number | null;
  rfq_http_status_counts?: Record<string, number>;
  rfq_coverage_window_ms?: number | null;
  /** WHI-974: CEX REST volume status (ok | geo_blocked | error). */
  cex_volume_status?: string | null;
  cex_volume_venue?: string | null;
  cex_volume_host?: string | null;
  cex_volume_http_status?: number | null;
  cex_volume_blocked_first_ms?: number | null;
  cex_volume_blocked_last_ms?: number | null;
};

/** One market card from GET /api/markets (WHI-774). */
export type MarketSummary = {
  id: string;
  display_name: string;
  has_rfq: boolean;
  cex_venue: string;
  dex_venue: string;
  pair_count: number;
  data_status: MarketDataStatus;
  db_path: string;
  health: HealthResponse;
};

export type MarketsResponse = {
  default_market_id: string;
  poll_interval_s: number;
  markets: MarketSummary[];
};

/** Client-side sort keys (subset of TUI SortKey + pair name + PnL WHI-824). */
export type SortKey =
  | "pair_id"
  | "net_edge"
  | "amm_spread"
  | "rfq_spread"
  | "bybit_mid"
  | "volume_24h"
  | "trades_24h"
  | "cex_volume_24h"
  | "dex_volume_24h"
  | "volume_ratio"
  | "premium_bps"
  | "amm_premium"
  | "underlying_price"
  | "tvl_usd"
  /** Optimal size net PnL in USD (column default; “where the money is”). */
  | "pnl_optimal_usd"
  /** Optimal size net PnL in bps (size-normalized efficiency). */
  | "pnl_optimal_bps";
