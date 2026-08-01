"""Live Textual panel for Bybit ⇄ Fluxion xStocks (M5 / WHI-734).

Public seams (tests depend on these, not Textual widgets):

- ``load_tui_config`` — typed ``config/tui.yaml``
- ``JournalReader`` — SQLite → ``monitor.quotes`` ticks
- ``build_overview`` / ``build_pair_detail`` / ``build_pair_overview_row``
- ``amm_pool_from_tick`` — pool geometry for M3 edge
- ``RunningEdgeState`` — process-lifetime EdgeStats accumulation

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
from monitor.tui.reader import JournalReader, VolumeStats

__all__ = [
    "EdgePanel",
    "JournalReader",
    "OverviewModel",
    "PairDetailModel",
    "PairOverviewRow",
    "RunningEdgeState",
    "SortKey",
    "SpreadPoint",
    "TradeStreamRow",
    "TuiConfig",
    "TuiConfigError",
    "VolumeStats",
    "amm_pool_from_tick",
    "build_overview",
    "build_pair_detail",
    "build_pair_overview_row",
    "default_tui_path",
    "load_tui_config",
    "observe_edges",
]
