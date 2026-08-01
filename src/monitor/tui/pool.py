"""Build AmmPoolState from pairs.yaml + a live pool tick (no RPC)."""

from __future__ import annotations

from monitor.fluxion.abi import USDC_DECIMALS, WRAPPER_DECIMALS_DEFAULT
from monitor.metrics.amm_pool import AmmPoolState
from monitor.quotes import FluxionPoolStateTick
from monitor.symbols.models import Pair


def amm_pool_from_tick(pair: Pair, tick: FluxionPoolStateTick) -> AmmPoolState | None:
    """Lift collector pool state into metrics slip geometry.

    Returns None when the pair has no AMM or fee is missing.
    Token order is taken from the tick (authoritative slot0 side); decimals
    use the same defaults as ``monitor.fluxion.pools`` (USDC 6 / wrapper 18).
    """
    amm = pair.fluxion.amm
    if amm is None:
        return None
    quote = pair.fluxion.quote_token_address.lower()
    t0 = tick.token0.lower()
    t1 = tick.token1.lower()
    if t0 == quote:
        token0_is_quote = True
        token0_decimals, token1_decimals = USDC_DECIMALS, WRAPPER_DECIMALS_DEFAULT
    elif t1 == quote:
        token0_is_quote = False
        token0_decimals, token1_decimals = WRAPPER_DECIMALS_DEFAULT, USDC_DECIMALS
    else:
        # Unexpected pool composition — cannot size slip safely.
        return None
    return AmmPoolState(
        pool_fee=amm.fee,
        sqrt_price_x96=tick.sqrt_price_x96,
        liquidity=tick.liquidity,
        token0_is_quote=token0_is_quote,
        token0_decimals=token0_decimals,
        token1_decimals=token1_decimals,
    )


def quote_is_token0(pair: Pair, tick: FluxionPoolStateTick) -> bool | None:
    """Whether the pool's token0 is the quote asset (for swap notional)."""
    quote = pair.fluxion.quote_token_address.lower()
    t0 = tick.token0.lower()
    t1 = tick.token1.lower()
    if t0 == quote:
        return True
    if t1 == quote:
        return False
    return None
