"""Edge / wear metrics and US equity session segmentation (M3 / WHI-732).

Public seams (tests and TUI depend on these, not internals):

- ``load_metrics_config`` — typed ``config/metrics.yaml``
- ``spread_bps`` — Bybit mid vs AMM / RFQ mid
- ``compute_edge`` / ``compute_edge_ladder`` — net paper edge + cost breakdown
- ``is_us_rth_open`` / ``session_kind`` — NYSE open / closed / early-close
- ``EdgeStats`` — cumulative P50/P95/P99/max + cost-floor breach duration/count

Depends on ``monitor.quotes`` tick shapes only (DESIGN §4.3), not WS/RPC clients.
"""

from monitor.metrics.config import (
    MetricsConfig,
    MetricsConfigError,
    default_metrics_path,
    load_metrics_config,
)
from monitor.metrics.edge import (
    CostBreakdown,
    EdgeResult,
    VenueKind,
    compute_edge,
    compute_edge_ladder,
    mid_from_bid_ask,
    spread_bps,
)
from monitor.metrics.session import SessionKind, is_us_rth_open, session_kind
from monitor.metrics.snapshot import (
    EdgeSnapshot,
    SpreadSnapshot,
    build_edge_snapshot,
    build_spread_snapshot,
)
from monitor.metrics.stats import BreachStats, Distribution, EdgeStats, SessionBuckets

__all__ = [
    "BreachStats",
    "CostBreakdown",
    "Distribution",
    "EdgeResult",
    "EdgeSnapshot",
    "EdgeStats",
    "MetricsConfig",
    "MetricsConfigError",
    "SessionBuckets",
    "SessionKind",
    "SpreadSnapshot",
    "VenueKind",
    "build_edge_snapshot",
    "build_spread_snapshot",
    "compute_edge",
    "compute_edge_ladder",
    "default_metrics_path",
    "is_us_rth_open",
    "load_metrics_config",
    "mid_from_bid_ask",
    "session_kind",
    "spread_bps",
]
