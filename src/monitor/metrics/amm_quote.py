"""AMM mid quotability — single seam for empty / untradable pools (WHI-795).

Uniswap V3 ``slot0.sqrtPriceX96`` survives after all in-range liquidity is
withdrawn. Residual mid is **not** a tradable quote: any size is unfillable,
and comparing it to CEX produces phantom spreads (e.g. +1016 bps / −10000 bps).

All consumers that surface AMM mid for price, spread, premium, net edge, or
bucket PnL must go through :func:`quotable_amm_mid` so columns cannot disagree
with fillability.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from monitor.quotes import FluxionPoolStateTick

# Wire reason codes (stable API / journal-adjacent). UI maps to human labels.
AmmQuoteReason = Literal["empty_pool", "invalid_mid"]


def quotable_amm_mid(
    tick: FluxionPoolStateTick | None,
) -> tuple[Decimal | None, AmmQuoteReason | None]:
    """Return ``(mid, reason)`` for AMM quote use.

    * ``tick is None`` → ``(None, None)`` — caller distinguishes no-tick /
      no-pool / stale via its own inventory and freshness paths.
    * ``liquidity <= 0`` → ``(None, "empty_pool")`` — residual slot0 mid discarded.
    * ``mid_usdc_per_native <= 0`` → ``(None, "invalid_mid")``.
    * else → ``(mid, None)``.
    """
    if tick is None:
        return None, None
    if tick.liquidity <= 0:
        return None, "empty_pool"
    mid = tick.mid_usdc_per_native
    if mid <= 0:
        return None, "invalid_mid"
    return mid, None


def is_pool_quotable(tick: FluxionPoolStateTick | None) -> bool:
    """True when the tick yields a positive mid with in-range liquidity."""
    mid, _reason = quotable_amm_mid(tick)
    return mid is not None


__all__ = [
    "AmmQuoteReason",
    "is_pool_quotable",
    "quotable_amm_mid",
]
