"""Edge / wear metrics, session segmentation, and PnL v2 cash-flow engine.

Public seams (tests and TUI/Web depend on these, not internals):

- ``load_metrics_config`` — typed ``config/metrics.yaml``
- ``spread_bps`` — Bybit mid vs AMM / RFQ mid
- ``compute_edge`` / ``compute_edge_ladder`` — M3 net paper edge + cost breakdown
- ``compute_pnl_usd`` / ``pnl_bucket_table`` / ``optimal_size`` — PnL v2 (WHI-756)
- ``build_pnl_pair_snapshot`` — journal ticks → dual-direction tables (WHI-766)
- ``annotate_drift`` / ``drift_wire_for_pair`` — sequential bar k×σ (WHI-962)
- ``build_capture_pair_snapshot`` / ``compute_capture_from_samples`` — occupancy
  capture rate (WHI-963)
- ``is_us_rth_open`` / ``session_kind`` — NYSE open / closed / early-close
- ``EdgeStats`` / ``OptimalPnlStats`` — time-weighted distributions + breaches
- ``build_spread_snapshot`` / ``build_edge_snapshot`` — tick → panel model (M5 feeds)

Depends on ``monitor.quotes`` tick shapes (DESIGN §4.3), not on WS/RPC clients.
Pair-shaped pool helpers also read inventory models (``Pair`` / ``BStocksPair``);
the pure ``amm_pool_from_tick`` stays inventory-free.
"""

from monitor.metrics.amm_pool import (
    AmmPoolState,
    amm_pool_from_pair_tick,
    amm_pool_from_tick,
)
from monitor.metrics.amm_quote import (
    AmmQuoteReason,
    amm_quote_for_cex,
    annotate_pricing_anomaly,
    is_tradable_amm_quote,
    quotable_amm_mid,
)
from monitor.metrics.capture import (
    CapturePairSnapshot,
    build_capture_pair_snapshot,
    compute_capture_from_samples,
)
from monitor.metrics.config import (
    CaptureConfig,
    MetricsConfig,
    MetricsConfigError,
    PnlV2Config,
    default_metrics_path,
    load_metrics_config,
)
from monitor.metrics.drift import (
    DriftAnnotation,
    annotate_drift,
    clears_drift,
    drift_premium_bps,
    drift_wire_for_pair,
    net_bps_from_pnl_tables,
    select_sigma_bps,
)
from monitor.metrics.edge import (
    CostBreakdown,
    EdgeResult,
    VenueKind,
    WithdrawalFeeKind,
    WithdrawalFeeParams,
    basis_wear_bps,
    best_net_edge,
    compute_edge,
    compute_edge_ladder,
    mid_from_bid_ask,
    spread_bps,
    withdrawal_fee_bps,
    withdrawal_fee_usd_for_direction,
)
from monitor.metrics.pnl_snapshot import (
    PnlOptimalSummary,
    PnlPairSnapshot,
    build_pnl_pair_snapshot,
    levels_from_depth_curve,
    overview_pnl_summary,
    rfq_tick_to_poll_quote,
)
from monitor.metrics.pnl_v2 import (
    OptimalSizeResult,
    PnlBucketTable,
    PnlCostBreakdownUsd,
    PnlResult,
    RfqPollQuote,
    compute_pnl_usd,
    optimal_size,
    pnl_bucket_table,
)
from monitor.metrics.premium import (
    PremiumSnapshot,
    build_premium_snapshot,
    equity_equivalent_mid,
    mean_mid,
    premium_bps,
    premium_type_label,
    reclassify_underlying_for_display,
)
from monitor.metrics.session import SessionKind, is_us_rth_open, session_kind
from monitor.metrics.snapshot import (
    EdgeSnapshot,
    SpreadSnapshot,
    build_edge_snapshot,
    build_spread_snapshot,
)
from monitor.metrics.stats import (
    BreachStats,
    Distribution,
    EdgeStats,
    OptimalPnlStats,
    SessionBuckets,
)
from monitor.metrics.withdrawal import withdrawal_params_from_pair

__all__ = [
    "AmmPoolState",
    "AmmQuoteReason",
    "BreachStats",
    "CaptureConfig",
    "CapturePairSnapshot",
    "CostBreakdown",
    "Distribution",
    "DriftAnnotation",
    "EdgeResult",
    "EdgeSnapshot",
    "EdgeStats",
    "MetricsConfig",
    "MetricsConfigError",
    "OptimalPnlStats",
    "OptimalSizeResult",
    "PnlBucketTable",
    "PnlCostBreakdownUsd",
    "PnlOptimalSummary",
    "PnlPairSnapshot",
    "PnlResult",
    "PnlV2Config",
    "PremiumSnapshot",
    "RfqPollQuote",
    "SessionBuckets",
    "SessionKind",
    "SpreadSnapshot",
    "VenueKind",
    "WithdrawalFeeKind",
    "WithdrawalFeeParams",
    "amm_pool_from_pair_tick",
    "amm_pool_from_tick",
    "amm_quote_for_cex",
    "annotate_drift",
    "annotate_pricing_anomaly",
    "basis_wear_bps",
    "best_net_edge",
    "build_capture_pair_snapshot",
    "build_edge_snapshot",
    "build_pnl_pair_snapshot",
    "build_premium_snapshot",
    "build_spread_snapshot",
    "clears_drift",
    "compute_capture_from_samples",
    "compute_edge",
    "compute_edge_ladder",
    "compute_pnl_usd",
    "default_metrics_path",
    "drift_premium_bps",
    "drift_wire_for_pair",
    "equity_equivalent_mid",
    "is_tradable_amm_quote",
    "is_us_rth_open",
    "levels_from_depth_curve",
    "load_metrics_config",
    "mean_mid",
    "mid_from_bid_ask",
    "net_bps_from_pnl_tables",
    "optimal_size",
    "overview_pnl_summary",
    "pnl_bucket_table",
    "premium_bps",
    "premium_type_label",
    "quotable_amm_mid",
    "reclassify_underlying_for_display",
    "rfq_tick_to_poll_quote",
    "select_sigma_bps",
    "session_kind",
    "spread_bps",
    "withdrawal_fee_bps",
    "withdrawal_fee_usd_for_direction",
    "withdrawal_params_from_pair",
]
