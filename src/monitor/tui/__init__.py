"""Live Textual panel for Bybit ⇄ Fluxion xStocks (M5 / WHI-734).

Public seams (tests depend on these, not Textual widgets):

- ``load_tui_config`` / ``validate_tui_against_metrics`` — typed ``config/tui.yaml``
- ``build_overview`` / ``build_pair_detail`` / ``build_pair_overview_row``
- ``amm_pool_from_tick`` — pool geometry for M3 edge
- ``RunningEdgeState`` — process-lifetime EdgeStats accumulation

Journal reads go through ``monitor.storage.JournalReader`` — SQLite schema
knowledge stays in ``monitor.storage`` (DESIGN §4.2).

Entry point: ``python -m monitor.tui`` (optional ``--db path``).

Library choice (DESIGN §4.1 / §8): **Textual** for interactive two-level
navigation (overview DataTable + detail screen). Numbers are produced only by
``monitor.metrics`` and ``monitor.attribution`` pure builders.
"""

from monitor.tui.builder import (
    build_overview,
    build_pair_detail,
    build_pair_overview_row,
    observe_edges,
)
from monitor.tui.config import (
    SortKey,
    TuiConfig,
    TuiConfigError,
    default_tui_path,
    load_tui_config,
    validate_tui_against_metrics,
)
from monitor.tui.model import (
    EdgePanel,
    OverviewModel,
    PairDetailModel,
    PairOverviewRow,
    RunningEdgeState,
    SpreadPoint,
    TradeStreamRow,
)
from monitor.tui.pool import amm_pool_from_tick

__all__ = [
    "EdgePanel",
    "OverviewModel",
    "PairDetailModel",
    "PairOverviewRow",
    "RunningEdgeState",
    "SortKey",
    "SpreadPoint",
    "TradeStreamRow",
    "TuiConfig",
    "TuiConfigError",
    "amm_pool_from_tick",
    "build_overview",
    "build_pair_detail",
    "build_pair_overview_row",
    "default_tui_path",
    "load_tui_config",
    "observe_edges",
    "validate_tui_against_metrics",
]
