"""Spread and net paper-edge with full cost breakdown (DESIGN §2.3).

Two-sided inventory paper arb:

    edge_bps = direction_aware_spread_bps
             - bybit_taker_bps
             - fluxion_fee_bps          (AMM only; RFQ embeds fee in quote)
             - bybit_slip_bps(Q)
             - fluxion_slip_bps(Q)      (AMM exact; RFQ 0 at quoted size)
             - gas_bps(Q)
             - usdt_usdc_basis_bps      (optional; default 0)

Directions (relative to base xStock):

- ``buy_fluxion_sell_bybit``: buy cheap on Fluxion, sell rich on Bybit
  (gross = (bybit_mid - fluxion_mid) / bybit_mid * 1e4)
- ``buy_bybit_sell_fluxion``: buy on Bybit, sell on Fluxion
  (gross = (fluxion_mid - bybit_mid) / bybit_mid * 1e4)

Reference mid for bps is always the de-multiplied Bybit mid so series are
comparable across venues.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from monitor.metrics.amm_slip import (
    fee_bps_from_pool_fee,
    fluxion_amm_slip_bps,
    gas_bps,
)
from monitor.metrics.bybit_slip import bybit_slip_bps
from monitor.metrics.config import MetricsConfig

VenueKind = Literal["amm", "rfq"]
Direction = Literal["buy_fluxion_sell_bybit", "buy_bybit_sell_fluxion"]


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    bybit_taker_bps: Decimal
    fluxion_fee_bps: Decimal
    bybit_slip_bps: Decimal
    fluxion_slip_bps: Decimal
    gas_bps: Decimal
    basis_bps: Decimal

    @property
    def total_wear_bps(self) -> Decimal:
        return (
            self.bybit_taker_bps
            + self.fluxion_fee_bps
            + self.bybit_slip_bps
            + self.fluxion_slip_bps
            + self.gas_bps
            + self.basis_bps
        )


@dataclass(frozen=True, slots=True)
class EdgeResult:
    pair_id: str
    venue: VenueKind
    direction: Direction
    size_usd: Decimal
    bybit_mid: Decimal
    fluxion_mid: Decimal
    gross_spread_bps: Decimal
    costs: CostBreakdown
    net_edge_bps: Decimal
    fillable: bool
    reason: str | None = None


def mid_from_bid_ask(bid: Decimal, ask: Decimal) -> Decimal:
    if bid <= 0 or ask <= 0:
        raise ValueError("bid and ask must be positive")
    return (bid + ask) / 2


def spread_bps(bybit_mid: Decimal, other_mid: Decimal) -> Decimal:
    """Signed (other - bybit) / bybit in bps. Positive ⇒ other richer than Bybit."""
    if bybit_mid <= 0 or other_mid <= 0:
        raise ValueError("mids must be positive")
    return (other_mid - bybit_mid) / bybit_mid * Decimal(10_000)


def direction_aware_gross_bps(
    bybit_mid: Decimal,
    fluxion_mid: Decimal,
    direction: Direction,
) -> Decimal:
    """Gross paper spread for one direction, in bps of Bybit mid."""
    if bybit_mid <= 0 or fluxion_mid <= 0:
        raise ValueError("mids must be positive")
    if direction == "buy_fluxion_sell_bybit":
        return (bybit_mid - fluxion_mid) / bybit_mid * Decimal(10_000)
    return (fluxion_mid - bybit_mid) / bybit_mid * Decimal(10_000)


def compute_edge(
    *,
    pair_id: str,
    bybit_bid: Decimal,
    bybit_ask: Decimal,
    fluxion_mid: Decimal,
    size_usd: Decimal,
    direction: Direction,
    venue: VenueKind,
    config: MetricsConfig,
    # AMM-only (ignored for RFQ):
    pool_fee: int | None = None,
    sqrt_price_x96: int | None = None,
    liquidity: int | None = None,
    token0_is_quote: bool = True,
    token0_decimals: int = 6,
    token1_decimals: int = 18,
    # Optional Bybit depth for VWAP; L1 half-spread when None.
    bybit_depth: list[tuple[Decimal, Decimal]] | None = None,
) -> EdgeResult:
    """Compute net paper edge for one pair × venue × direction × size."""
    bybit_mid = mid_from_bid_ask(bybit_bid, bybit_ask)
    gross = direction_aware_gross_bps(bybit_mid, fluxion_mid, direction)

    # Bybit leg is the opposite of Fluxion leg.
    if direction == "buy_fluxion_sell_bybit":
        bybit_dir = "sell"
        fluxion_dir = "buy"
    else:
        bybit_dir = "buy"
        fluxion_dir = "sell"

    b_slip = bybit_slip_bps(
        bid=bybit_bid,
        ask=bybit_ask,
        size_usd=size_usd,
        direction=bybit_dir,
        depth=bybit_depth,
    )
    if b_slip is None:
        costs = CostBreakdown(
            bybit_taker_bps=Decimal(str(config.bybit_taker_fee_bps)),
            fluxion_fee_bps=Decimal(0),
            bybit_slip_bps=Decimal(0),
            fluxion_slip_bps=Decimal(0),
            gas_bps=gas_bps(Decimal(str(config.gas_usd_per_swap)), size_usd),
            basis_bps=Decimal(str(config.usdt_usdc_basis_bps)),
        )
        return EdgeResult(
            pair_id=pair_id,
            venue=venue,
            direction=direction,
            size_usd=size_usd,
            bybit_mid=bybit_mid,
            fluxion_mid=fluxion_mid,
            gross_spread_bps=gross,
            costs=costs,
            net_edge_bps=gross - costs.total_wear_bps,
            fillable=False,
            reason="bybit_book_unfillable",
        )

    if venue == "amm":
        if pool_fee is None or sqrt_price_x96 is None or liquidity is None:
            raise ValueError("amm venue requires pool_fee, sqrt_price_x96, liquidity")
        fee = fee_bps_from_pool_fee(pool_fee)
        amm = fluxion_amm_slip_bps(
            sqrt_price_x96=sqrt_price_x96,
            liquidity=liquidity,
            pool_fee=pool_fee,
            size_usd=size_usd,
            direction=fluxion_dir,
            token0_is_quote=token0_is_quote,
            token0_decimals=token0_decimals,
            token1_decimals=token1_decimals,
        )
        f_slip = amm.slip_bps_vs_mid
        fillable = amm.fillable
        reason = amm.reason
    else:
        # RFQ quote is already an executable mid at the polled notional; no
        # separate pool fee / AMM impact line (MM embeds costs in the quote).
        fee = Decimal(0)
        f_slip = Decimal(0)
        fillable = True
        reason = None

    costs = CostBreakdown(
        bybit_taker_bps=Decimal(str(config.bybit_taker_fee_bps)),
        fluxion_fee_bps=fee,
        bybit_slip_bps=b_slip,
        fluxion_slip_bps=f_slip,
        gas_bps=gas_bps(Decimal(str(config.gas_usd_per_swap)), size_usd),
        basis_bps=Decimal(str(config.usdt_usdc_basis_bps)),
    )
    net = gross - costs.total_wear_bps
    return EdgeResult(
        pair_id=pair_id,
        venue=venue,
        direction=direction,
        size_usd=size_usd,
        bybit_mid=bybit_mid,
        fluxion_mid=fluxion_mid,
        gross_spread_bps=gross,
        costs=costs,
        net_edge_bps=net,
        fillable=fillable,
        reason=reason,
    )


def compute_edge_ladder(
    *,
    pair_id: str,
    bybit_bid: Decimal,
    bybit_ask: Decimal,
    fluxion_mid: Decimal,
    venue: VenueKind,
    config: MetricsConfig,
    pool_fee: int | None = None,
    sqrt_price_x96: int | None = None,
    liquidity: int | None = None,
    token0_is_quote: bool = True,
    token0_decimals: int = 6,
    token1_decimals: int = 18,
    bybit_depth: list[tuple[Decimal, Decimal]] | None = None,
    directions: tuple[Direction, ...] = (
        "buy_fluxion_sell_bybit",
        "buy_bybit_sell_fluxion",
    ),
) -> list[EdgeResult]:
    """All directions × size ladder for one venue snapshot."""
    out: list[EdgeResult] = []
    for direction in directions:
        for size in config.size_ladder_usd:
            out.append(
                compute_edge(
                    pair_id=pair_id,
                    bybit_bid=bybit_bid,
                    bybit_ask=bybit_ask,
                    fluxion_mid=fluxion_mid,
                    size_usd=Decimal(str(size)),
                    direction=direction,
                    venue=venue,
                    config=config,
                    pool_fee=pool_fee,
                    sqrt_price_x96=sqrt_price_x96,
                    liquidity=liquidity,
                    token0_is_quote=token0_is_quote,
                    token0_decimals=token0_decimals,
                    token1_decimals=token1_decimals,
                    bybit_depth=bybit_depth,
                )
            )
    return out


def best_net_edge(results: list[EdgeResult]) -> EdgeResult | None:
    """Best fillable net edge; None if nothing fillable."""
    fillable = [r for r in results if r.fillable]
    if not fillable:
        return None
    return max(fillable, key=lambda r: r.net_edge_bps)
