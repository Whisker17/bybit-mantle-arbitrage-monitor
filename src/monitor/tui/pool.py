"""Build AmmPoolState from market inventory + a live pool tick (no RPC).

Thin wrappers over ``monitor.metrics.amm_pool.amm_pool_from_tick`` so TUI/API
keep a pair-shaped call site. Supports Bybit⇄Fluxion ``Pair`` and
Binance⇄Pancake ``BStocksPair`` (WHI-773).
"""

from __future__ import annotations

from monitor.fluxion.abi import USDC_DECIMALS, WRAPPER_DECIMALS_DEFAULT
from monitor.metrics.amm_pool import AmmPoolState
from monitor.metrics.amm_pool import amm_pool_from_tick as amm_pool_from_components
from monitor.quotes import FluxionPoolStateTick
from monitor.symbols.bstocks_models import BStocksPair
from monitor.symbols.models import Pair

# Re-export pure seam name used by tests / API (pair-shaped overload).
__all__ = [
    "amm_pool_from_tick",
    "quote_is_token0",
]


def amm_pool_from_tick(
    pair: Pair | BStocksPair,
    tick: FluxionPoolStateTick,
    *,
    quote_decimals: int | None = None,
) -> AmmPoolState | None:
    """Lift collector pool state into metrics slip geometry.

    Returns None when the pair has no AMM. Token order is taken from the tick;
    decimals default to market conventions (Fluxion USDC=6 / wrapper=18;
    Pancake USDT=18 / native=inventory) unless ``quote_decimals`` is set.
    """
    if isinstance(pair, BStocksPair):
        pancake_amm = pair.pancake.amm
        if pancake_amm is None:
            return None
        q_dec = 18 if quote_decimals is None else quote_decimals
        return amm_pool_from_components(
            tick,
            quote_token_address=pair.pancake.quote_token_address,
            pool_fee=pancake_amm.fee,
            quote_decimals=q_dec,
            base_decimals=pair.pancake.native_decimals,
        )

    fluxion_amm = pair.fluxion.amm
    if fluxion_amm is None:
        return None
    q_dec = USDC_DECIMALS if quote_decimals is None else quote_decimals
    # Wrapper side uses default 18 (same as monitor.fluxion.pools); native
    # may differ but slip math walks the wrapper pool, not the native vault.
    return amm_pool_from_components(
        tick,
        quote_token_address=pair.fluxion.quote_token_address,
        pool_fee=fluxion_amm.fee,
        quote_decimals=q_dec,
        base_decimals=WRAPPER_DECIMALS_DEFAULT,
    )


def quote_is_token0(
    pair: Pair | BStocksPair, tick: FluxionPoolStateTick
) -> bool | None:
    """Whether the pool's token0 is the quote asset (for swap notional)."""
    if isinstance(pair, BStocksPair):
        quote = pair.pancake.quote_token_address.lower()
    else:
        quote = pair.fluxion.quote_token_address.lower()
    t0 = tick.token0.lower()
    t1 = tick.token1.lower()
    if t0 == quote:
        return True
    if t1 == quote:
        return False
    return None
