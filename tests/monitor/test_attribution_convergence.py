"""Seam: is_converging / bybit_move_aligned."""

from __future__ import annotations

from decimal import Decimal

from monitor.attribution import (
    bybit_move_aligned,
    is_converging,
    resolve_bybit_mid_prev,
)


def test_sell_converges_when_fluxion_above_bybit() -> None:
    assert (
        is_converging(
            "sell_native",
            fluxion_mid=Decimal("105"),
            bybit_mid=Decimal("100"),
        )
        is True
    )
    assert (
        is_converging(
            "buy_native",
            fluxion_mid=Decimal("105"),
            bybit_mid=Decimal("100"),
        )
        is False
    )


def test_buy_converges_when_fluxion_below_bybit() -> None:
    assert (
        is_converging(
            "buy_native",
            fluxion_mid=Decimal("95"),
            bybit_mid=Decimal("100"),
        )
        is True
    )
    assert (
        is_converging(
            "sell_native",
            fluxion_mid=Decimal("95"),
            bybit_mid=Decimal("100"),
        )
        is False
    )


def test_equal_mids_undefined() -> None:
    assert (
        is_converging(
            "buy_native",
            fluxion_mid=Decimal("100"),
            bybit_mid=Decimal("100"),
        )
        is None
    )


def test_non_positive_mids_undefined() -> None:
    assert (
        is_converging("buy_native", fluxion_mid=Decimal(0), bybit_mid=Decimal(1))
        is None
    )


def test_bybit_align_buy_after_bybit_up() -> None:
    assert (
        bybit_move_aligned(
            "buy_native",
            bybit_mid=Decimal("101"),
            bybit_mid_prev=Decimal("100"),
            min_move_bps=Decimal("1"),
        )
        is True
    )
    assert (
        bybit_move_aligned(
            "sell_native",
            bybit_mid=Decimal("101"),
            bybit_mid_prev=Decimal("100"),
            min_move_bps=Decimal("1"),
        )
        is False
    )


def test_bybit_align_sell_after_bybit_down() -> None:
    assert (
        bybit_move_aligned(
            "sell_native",
            bybit_mid=Decimal("99"),
            bybit_mid_prev=Decimal("100"),
            min_move_bps=Decimal("1"),
        )
        is True
    )


def test_bybit_align_ignores_tiny_move() -> None:
    # 0.5 bps move with 1 bps threshold → None
    assert (
        bybit_move_aligned(
            "buy_native",
            bybit_mid=Decimal("100.005"),
            bybit_mid_prev=Decimal("100"),
            min_move_bps=Decimal("1"),
        )
        is None
    )


def test_resolve_bybit_mid_prev_uses_lookback() -> None:
    series = [
        (1_000, Decimal("100")),
        (3_000, Decimal("101")),
        (8_000, Decimal("102")),
    ]
    # trade at 10_000, lookback 5000 → anchor 5000 → latest sample ≤5000 is ts=3000
    assert resolve_bybit_mid_prev(10_000, series, lookback_ms=5_000) == Decimal("101")
    assert resolve_bybit_mid_prev(10_000, series, lookback_ms=50_000) is None
