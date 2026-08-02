"""AMM pool geometry needed for edge / slip (no RPC)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from monitor.fluxion.abi import WRAPPER_DECIMALS_DEFAULT
from monitor.fluxion.pools import mid_from_sqrt_price_x96
from monitor.quotes import FluxionPoolStateTick
from monitor.symbols.bstocks_models import BStocksPair
from monitor.symbols.models import Pair


@dataclass(frozen=True, slots=True)
class AmmPoolState:
    """Concentrated-liquidity snapshot + topology for one UniV3-style pool.

    Callers build this from market inventory (fee, token order/decimals) plus a
    ``FluxionPoolStateTick`` (sqrt price, liquidity). Metrics never invent
    token0/token1 ordering defaults. Quote decimals are market-specific
    (Mantle USDC=6, BSC USDT=18) and come from ``dex.quote_decimals``.
    """

    pool_fee: int
    sqrt_price_x96: int
    liquidity: int
    token0_is_quote: bool
    token0_decimals: int
    token1_decimals: int

    @property
    def quote_decimals(self) -> int:
        return self.token0_decimals if self.token0_is_quote else self.token1_decimals

    @property
    def base_decimals(self) -> int:
        return self.token1_decimals if self.token0_is_quote else self.token0_decimals

    def mid_quote_per_base(self) -> Decimal:
        return mid_from_sqrt_price_x96(
            self.sqrt_price_x96,
            token0_is_quote=self.token0_is_quote,
            token0_decimals=self.token0_decimals,
            token1_decimals=self.token1_decimals,
        )


def amm_pool_from_tick(
    tick: FluxionPoolStateTick,
    *,
    quote_token_address: str,
    pool_fee: int,
    quote_decimals: int,
    base_decimals: int,
) -> AmmPoolState | None:
    """Lift a pool-state tick into metrics slip geometry.

    Pure: no inventory type coupling. Token order comes from the tick
    (authoritative slot0 side); decimals + fee from the market inventory.
    Returns None when the tick tokens do not match the quote address.
    """
    if pool_fee < 0:
        return None
    if quote_decimals < 0 or base_decimals < 0:
        return None
    quote = quote_token_address.lower()
    t0 = tick.token0.lower()
    t1 = tick.token1.lower()
    if t0 == quote:
        token0_is_quote = True
        token0_decimals, token1_decimals = quote_decimals, base_decimals
    elif t1 == quote:
        token0_is_quote = False
        token0_decimals, token1_decimals = base_decimals, quote_decimals
    else:
        return None
    return AmmPoolState(
        pool_fee=pool_fee,
        sqrt_price_x96=tick.sqrt_price_x96,
        liquidity=tick.liquidity,
        token0_is_quote=token0_is_quote,
        token0_decimals=token0_decimals,
        token1_decimals=token1_decimals,
    )


def _pair_pool_parts(
    pair: Pair | BStocksPair,
    *,
    quote_decimals: int | None,
) -> tuple[str, int, int, int] | None:
    """Return (quote_addr, pool_fee, quote_decimals, base_decimals) or None.

    When ``quote_decimals`` is omitted, defaults match market conventions
    (Fluxion USDC=6, Pancake USDT=18). Prefer passing ``dex.quote_decimals``.
    """
    if isinstance(pair, BStocksPair):
        # dex:none pairs (WHI-790) have pancake.amm is None.
        pancake_amm = pair.pancake.amm
        if pancake_amm is None:
            return None
        return (
            pair.pancake.quote_token_address,
            pancake_amm.fee,
            18 if quote_decimals is None else quote_decimals,
            pair.pancake.native_decimals,
        )
    fluxion_amm = pair.fluxion.amm
    if fluxion_amm is None:
        return None
    return (
        pair.fluxion.quote_token_address,
        fluxion_amm.fee,
        6 if quote_decimals is None else quote_decimals,
        WRAPPER_DECIMALS_DEFAULT,
    )


def amm_pool_from_pair_tick(
    pair: Pair | BStocksPair,
    tick: FluxionPoolStateTick,
    *,
    quote_decimals: int | None = None,
) -> AmmPoolState | None:
    """Pair-shaped wrapper: inventory fee/addresses + tick → ``AmmPoolState``.

    Prefer passing ``quote_decimals`` from ``MarketContext.dex.quote_decimals``
    so venue decimals stay config-injected. Defaults (USDC=6 / USDT=18) only
    apply when the caller omits them (legacy Bybit-only call sites).
    """
    parts = _pair_pool_parts(pair, quote_decimals=quote_decimals)
    if parts is None:
        return None
    quote_addr, fee, q_dec, b_dec = parts
    return amm_pool_from_tick(
        tick,
        quote_token_address=quote_addr,
        pool_fee=fee,
        quote_decimals=q_dec,
        base_decimals=b_dec,
    )


def quote_is_token0_for_pair(
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
