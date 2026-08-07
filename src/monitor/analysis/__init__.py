"""Offline analysis helpers (M8 edge quantification, research scripts).

Pure aggregation over PnL v2 / journal samples. Scripts own I/O.
"""

from monitor.analysis.delay_decay import (
    ClipSizeRow,
    DistStats,
    DriftPremiumFit,
    SequentialOutcome,
    clip_size_sweep,
    distribution_stats,
    fit_drift_premium_k,
    optimal_clip_size,
    portfolio_sequential_profit,
    sequential_capturable_profit,
    transit_sigma_bps,
)
from monitor.analysis.edge_quant import (
    EdgeSample,
    OpportunityWindow,
    SweepRow,
    ThresholdFit,
    capturable_profit_single_flight,
    detect_windows,
    fit_min_edge_bps,
    portfolio_capturable_profit,
    threshold_sweep,
)

__all__ = [
    "ClipSizeRow",
    "DistStats",
    "DriftPremiumFit",
    "EdgeSample",
    "OpportunityWindow",
    "SequentialOutcome",
    "SweepRow",
    "ThresholdFit",
    "capturable_profit_single_flight",
    "clip_size_sweep",
    "detect_windows",
    "distribution_stats",
    "fit_drift_premium_k",
    "fit_min_edge_bps",
    "optimal_clip_size",
    "portfolio_capturable_profit",
    "portfolio_sequential_profit",
    "sequential_capturable_profit",
    "threshold_sweep",
    "transit_sigma_bps",
]
