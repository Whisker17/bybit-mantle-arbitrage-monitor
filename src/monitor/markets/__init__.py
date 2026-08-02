"""Multi-market assembly (M7-2 / WHI-771).

A **market** is the explicit unit of configuration + storage:

    market = {id, cex, dex, costs, inventory, sqlite journal}

Bybit ⇄ Fluxion remains the default (``DEFAULT_MARKET_ID``). Algorithms under
``monitor.metrics`` / ``monitor.attribution`` stay market-agnostic; this package
is the assembly layer that loads the right inventory, fee/gas knobs, and
per-market SQLite path.
"""

from monitor.markets.context import (
    MarketContext,
    apply_market_costs,
    load_market_context,
)
from monitor.markets.ids import (
    DEFAULT_MARKET_ID,
    LEGACY_SQLITE_RELPATH,
    known_market_ids,
    market_sqlite_relpath,
    normalize_market_id,
)
from monitor.markets.load import (
    MarketConfigError,
    default_markets_dir,
    list_market_ids,
    load_market_file,
    market_file_path,
)
from monitor.markets.models import (
    CexSide,
    DexSide,
    MarketCosts,
    MarketFile,
    MultiplierSemantics,
)

__all__ = [
    "DEFAULT_MARKET_ID",
    "LEGACY_SQLITE_RELPATH",
    "CexSide",
    "DexSide",
    "MarketConfigError",
    "MarketContext",
    "MarketCosts",
    "MarketFile",
    "MultiplierSemantics",
    "apply_market_costs",
    "default_markets_dir",
    "known_market_ids",
    "list_market_ids",
    "load_market_context",
    "load_market_file",
    "market_file_path",
    "market_sqlite_relpath",
    "normalize_market_id",
]
