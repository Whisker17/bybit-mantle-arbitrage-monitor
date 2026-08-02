"""Edge / wear metrics, session segmentation, and PnL v2 cash-flow engine.

Public seams (tests and TUI/Web depend on these, not internals):

- ``load_metrics_config`` — typed ``config/metrics.yaml``
- ``spread_bps`` — Bybit mid vs AMM / RFQ mid
- ``compute_edge`` / ``compute_edge_ladder`` — M3 net paper edge + cost breakdown
- ``compute_pnl_usd`` / ``pnl_bucket_table`` / ``optimal_size`` — PnL v2 (WHI-756)
- ``build_pnl_pair_snapshot`` — journal ticks → dual-direction tables (WHI-766)
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
    MAX_SANE_ABS_BPS,
    AmmQuoteReason,
    assert_sane_bps,
    is_pool_quotable,
    quotable_amm_mid,
)
from monitor.metrics.config import (
    MetricsConfig,
    MetricsConfigError,
    PnlV2Config,
    default_metrics_path,
    load_metrics_config,
)
from monitor.metrics.edge import (
    CostBreakdown,
    EdgeResult,
    VenueKind,
    best_net_edge,
    compute_edge,
    compute_edge_ladder,
    mid_from_bid_ask,
    spread_bps,
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

__all__ = [
    "MAX_SANE_ABS_BPS",
    "AmmPoolState",
    "AmmQuoteReason",
    "BreachStats",
    "CostBreakdown",
    "Distribution",
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
    "amm_pool_from_pair_tick",
    "amm_pool_from_tick",
    "assert_sane_bps",
    "best_net_edge",
    "build_edge_snapshot",
    "build_pnl_pair_snapshot",
    "build_premium_snapshot",
    "build_spread_snapshot",
    "compute_edge",
    "compute_edge_ladder",
    "compute_pnl_usd",
    "default_metrics_path",
    "equity_equivalent_mid",
    "is_pool_quotable",
    "is_us_rth_open",
    "levels_from_depth_curve",
    "load_metrics_config",
    "mean_mid",
    "mid_from_bid_ask",
    "optimal_size",
    "overview_pnl_summary",
    "pnl_bucket_table",
    "premium_bps",
    "premium_type_label",
    "quotable_amm_mid",
    "reclassify_underlying_for_display",
    "rfq_tick_to_poll_quote",
    "session_kind",
    "spread_bps",
]
