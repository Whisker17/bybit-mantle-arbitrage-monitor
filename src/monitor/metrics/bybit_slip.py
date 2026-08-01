"""Bybit execution cost vs mid from L1 and optional depth levels.

Phase-1 note (mba/m3_bybit.py): slippage measured from mid already includes the
half-spread. Apply to historical mid, never on top of bid/ask, or the spread is
charged twice.

M2 collectors only stream L1 (bid1/ask1). Default path uses L1 half-spread as the
minimum take cost. When full book levels are supplied, walk VWAP for exact size.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from monitor.bybit.depth_math import book_vwap_for_notional

BookSide = Literal["bid", "ask"]
BybitLeg = Literal["buy", "sell"]

BPS = Decimal(10_000)


def half_spread_bps(bid: Decimal, ask: Decimal) -> Decimal:
    """Half top-of-book spread in bps relative to mid."""
    if bid <= 0 or ask <= 0:
        raise ValueError("bid and ask must be positive")
    if ask < bid:
        raise ValueError(f"crossed book bid={bid} ask={ask}")
    mid = (bid + ask) / 2
    return (ask - bid) / mid / 2 * BPS


def l1_slip_bps_from_mid(bid: Decimal, ask: Decimal) -> Decimal:
    """L1 take cost vs mid: equals half-spread (fill entirely at bid or ask)."""
    return half_spread_bps(bid, ask)


def book_vwap_slip_bps(
    levels: list[tuple[Decimal, Decimal]],
    *,
    size_usd: Decimal,
    mid: Decimal,
    side: BookSide,
) -> Decimal | None:
    """Walk ``levels`` (price, size) for ``size_usd`` notional; slip vs mid in bps.

    ``side`` is the book side consumed: ``\"bid\"`` when selling base into bids,
    ``\"ask\"`` when buying base from asks. Returns None if the book cannot fill.
    """
    if size_usd <= 0 or mid <= 0:
        raise ValueError("size_usd and mid must be positive")
    if side not in ("bid", "ask"):
        raise ValueError(f"side must be bid|ask, got {side!r}")

    vwap = book_vwap_for_notional(levels, size_usd)
    if vwap is None:
        return None
    if side == "bid":
        # Selling: worse (lower) than mid → positive slip fraction.
        slip = (mid - vwap) / mid
    else:
        slip = (vwap - mid) / mid
    return slip * BPS


def bybit_slip_bps(
    *,
    bid: Decimal,
    ask: Decimal,
    size_usd: Decimal,
    direction: BybitLeg,
    depth: list[tuple[Decimal, Decimal]] | None = None,
) -> Decimal | None:
    """Bybit slip vs mid for one leg.

    ``direction`` is the *Bybit* leg: ``\"buy\"`` (lift asks) or ``\"sell\"``
    (hit bids). Without depth, returns L1 half-spread (assumes size fits L1).
    With depth, returns VWAP slip or None if unfillable.
    """
    if direction not in ("buy", "sell"):
        raise ValueError(f"direction must be buy|sell, got {direction!r}")
    mid = (bid + ask) / 2
    if depth is None:
        # L1 model: fill at top of book; slip vs mid = half-spread.
        return l1_slip_bps_from_mid(bid, ask)
    side: BookSide = "ask" if direction == "buy" else "bid"
    return book_vwap_slip_bps(depth, size_usd=size_usd, mid=mid, side=side)
