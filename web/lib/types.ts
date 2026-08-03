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
  | "no_depth"
  | "no_fillable"
  | "stale";

/** Why AMM mid is n/a when a pool tick existed (WHI-795). */
export type AmmQuoteReason = "empty_pool" | "invalid_mid";

/**
 * Why a row is denied a Top-N seat (WHI-796). UI maps via format helpers;
 * eligibility lives in web/lib/sort.ts.
 */
export type DexNonTradeableReason =
  | "empty_pool"
  | "invalid_mid"
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
  /** WHI-821: true when any leg exceeds quote_max_age_ms (quiet ≠ dead). */
  quote_aged?: boolean;
  cex_quote_age_ms?: number | null;
  amm_quote_age_ms?: number | null;
  depth_quote_age_ms?: number | null;
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
  /** WHI-821: per-leg recv ages; null when API omitted now_ms. */
  cex_quote_age_ms?: number | null;
  amm_quote_age_ms?: number | null;
  depth_quote_age_ms?: number | null;
  quote_aged?: boolean;
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

/** Client-side sort keys (subset of TUI SortKey + pair name). */
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
  | "tvl_usd";
