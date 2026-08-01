"""AMM pool geometry needed for edge / slip (no RPC)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from monitor.fluxion.pools import mid_from_sqrt_price_x96


@dataclass(frozen=True, slots=True)
class AmmPoolState:
    """Concentrated-liquidity snapshot + topology for one Fluxion V3 pool.

    Callers build this from ``pairs.yaml`` (fee, token order/decimals) plus a
    ``FluxionPoolStateTick`` (sqrt price, liquidity). Metrics never invent
    token0/token1 ordering defaults.
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
