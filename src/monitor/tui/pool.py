"""TUI-facing re-exports for pool geometry (pair-shaped call sites).

Implementation lives in ``monitor.metrics.amm_pool`` so new-market support does
not grow the frozen TUI package (AGENTS.md). Prefer importing from
``monitor.metrics`` for new code.
"""

from __future__ import annotations

from monitor.metrics.amm_pool import (
    amm_pool_from_pair_tick as amm_pool_from_tick,
)
from monitor.metrics.amm_pool import (
    quote_is_token0_for_pair as quote_is_token0,
)

__all__ = [
    "amm_pool_from_tick",
    "quote_is_token0",
]
