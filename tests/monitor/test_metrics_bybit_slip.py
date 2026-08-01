"""Seam: Bybit L1 + depth VWAP slip vs mid."""

from __future__ import annotations

from decimal import Decimal

from monitor.metrics.bybit_slip import book_vwap_slip_bps, bybit_slip_bps


def test_depth_vwap_sell_into_bids() -> None:
    # mid 100; bids 99.9 x 10 and 99.0 x 100 (sizes in base)
    mid = Decimal(100)
    levels = [(Decimal("99.9"), Decimal(10)), (Decimal("99.0"), Decimal(100))]
    # $500 notional fills entirely in first level: 500/99.9 ≈ 5.005 qty
    # vwap = 99.9; slip = (100-99.9)/100 * 1e4 = 10 bps
    slip = book_vwap_slip_bps(levels, size_usd=Decimal(500), mid=mid, side="bid")
    assert slip == Decimal(10)


def test_depth_unfillable() -> None:
    levels = [(Decimal("99.9"), Decimal("0.1"))]  # only $9.99 depth
    slip = book_vwap_slip_bps(
        levels, size_usd=Decimal(1000), mid=Decimal(100), side="bid"
    )
    assert slip is None


def test_bybit_slip_uses_depth_when_provided() -> None:
    bid, ask = Decimal("99.9"), Decimal("100.1")
    # Without depth: L1 half-spread
    l1 = bybit_slip_bps(
        bid=bid, ask=ask, size_usd=Decimal(1000), direction="sell", depth=None
    )
    assert l1 is not None
    # With thin book: unfillable
    thin = bybit_slip_bps(
        bid=bid,
        ask=ask,
        size_usd=Decimal(1000),
        direction="sell",
        depth=[(bid, Decimal("0.01"))],
    )
    assert thin is None
