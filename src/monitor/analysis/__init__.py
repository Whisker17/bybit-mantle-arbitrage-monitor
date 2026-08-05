"""Offline analysis helpers (M8 edge quantification, research scripts).

Pure aggregation over PnL v2 / journal samples. Scripts own I/O.
"""

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
    "EdgeSample",
    "OpportunityWindow",
    "SweepRow",
    "ThresholdFit",
    "capturable_profit_single_flight",
    "detect_windows",
    "fit_min_edge_bps",
    "portfolio_capturable_profit",
    "threshold_sweep",
]
