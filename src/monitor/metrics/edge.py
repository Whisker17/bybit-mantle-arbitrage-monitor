"""Spread and net paper-edge with full cost breakdown (DESIGN §2.3).

Two-sided inventory paper arb:

    edge_bps = direction_aware_spread_bps
             - bybit_taker_bps
             - fluxion_fee_bps          (AMM only; RFQ embeds fee in quote)
             - bybit_slip_bps(Q)
             - fluxion_slip_bps(Q)      (AMM exact; RFQ 0 at quoted size)
             - gas_bps(Q)
             - signed_basis_bps         (USDC premium; + when paying USDC)

Directions (relative to base xStock):

- ``buy_fluxion_sell_bybit``: buy cheap on Fluxion, sell rich on Bybit
- ``buy_bybit_sell_fluxion``: buy on Bybit, sell on Fluxion

Reference mid for bps is always the de-multiplied Bybit mid.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from monitor.metrics.amm_pool import AmmPoolState
from monitor.metrics.amm_slip import (
    FluxionLeg,
    fee_bps_from_pool_fee,
    fluxion_amm_slip_bps,
    gas_bps,
)
from monitor.metrics.bybit_slip import BPS, BybitLeg, bybit_slip_bps
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
    return (other_mid - bybit_mid) / bybit_mid * BPS


def direction_aware_gross_bps(
    bybit_mid: Decimal,
    fluxion_mid: Decimal,
    direction: Direction,
) -> Decimal:
    """Gross paper spread for one direction, in bps of Bybit mid."""
    if bybit_mid <= 0 or fluxion_mid <= 0:
        raise ValueError("mids must be positive")
    if direction == "buy_fluxion_sell_bybit":
        return (bybit_mid - fluxion_mid) / bybit_mid * BPS
    return (fluxion_mid - bybit_mid) / bybit_mid * BPS


def basis_wear_bps(
    usdt_usdc_basis_bps: Decimal,
    direction: Direction,
) -> Decimal:
    """Sign-aware basis wear from a USDC-over-USDT premium (bps).

    DESIGN §2.6.2 / WHI-960: paying USDC is **charged** the premium; receiving
    USDC is **credited**. ``usdt_usdc_basis_bps`` is the measured premium of
    USDC vs USDT (positive when USDC > USDT, e.g. ~7.5). Breakdown keeps the
    signed line so the UI can render a credit as negative wear.
    """
    if direction == "buy_fluxion_sell_bybit":
        return usdt_usdc_basis_bps
    return -usdt_usdc_basis_bps


def _costs(
    config: MetricsConfig,
    *,
    size_usd: Decimal,
    bybit_slip: Decimal,
    fluxion_fee: Decimal,
    fluxion_slip: Decimal,
    direction: Direction,
) -> CostBreakdown:
    return CostBreakdown(
        bybit_taker_bps=config.bybit_taker_fee_bps,
        fluxion_fee_bps=fluxion_fee,
        bybit_slip_bps=bybit_slip,
        fluxion_slip_bps=fluxion_slip,
        gas_bps=gas_bps(config.gas_usd_per_swap, size_usd),
        basis_bps=basis_wear_bps(config.usdt_usdc_basis_bps, direction),
    )


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
    amm: AmmPoolState | None = None,
    bybit_depth: list[tuple[Decimal, Decimal]] | None = None,
) -> EdgeResult:
    """Compute net paper edge for one pair × venue × direction × size.

    AMM venue requires ``amm``. RFQ venue ignores AMM geometry; ``fluxion_mid``
    must be the side-matching executable quote for ``direction``.
    """
    bybit_mid = mid_from_bid_ask(bybit_bid, bybit_ask)
    gross = direction_aware_gross_bps(bybit_mid, fluxion_mid, direction)

    bybit_dir: BybitLeg
    fluxion_dir: FluxionLeg
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
        costs = _costs(
            config,
            size_usd=size_usd,
            bybit_slip=Decimal(0),
            fluxion_fee=Decimal(0),
            fluxion_slip=Decimal(0),
            direction=direction,
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
        if amm is None:
            raise ValueError("amm venue requires AmmPoolState")
        fee = fee_bps_from_pool_fee(amm.pool_fee)
        swap = fluxion_amm_slip_bps(amm, size_usd=size_usd, direction=fluxion_dir)
        f_slip = swap.slip_bps_vs_mid
        fillable = swap.fillable
        reason = swap.reason
    else:
        # RFQ: MM embeds fee/impact in the quote. Ladder sizes other than the
        # polled notional still use this price with zero extra slip — the TUI
        # should prefer the size nearest the RFQ poll notional.
        fee = Decimal(0)
        f_slip = Decimal(0)
        fillable = True
        reason = None

    costs = _costs(
        config,
        size_usd=size_usd,
        bybit_slip=b_slip,
        fluxion_fee=fee,
        fluxion_slip=f_slip,
        direction=direction,
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
    amm: AmmPoolState | None = None,
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
                    size_usd=size,
                    direction=direction,
                    venue=venue,
                    config=config,
                    amm=amm,
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
