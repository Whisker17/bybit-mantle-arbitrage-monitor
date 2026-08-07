"""Unit tests for on-chain fill-validation classification (WHI-908)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from monitor.analysis.fill_validation import (
    DecodedSwapView,
    abs_basis_bps,
    classify_window,
    depth_supports_size,
    matching_profitable_swaps,
    pool_age_stats,
    profitable_swap_direction,
    swap_notional_usd,
    swaps_in_window,
    virtual_quote_side_usd,
)
from monitor.metrics.amm_pool import AmmPoolState


def _swap(
    *,
    ts: int,
    direction: str = "buy_native",
    pair: str = "HOODx",
    notional: str | int = 500,
    price: str | None = "120",
    block: int = 1,
) -> DecodedSwapView:
    n = Decimal(notional)
    return DecodedSwapView(
        pair_id=pair,
        recv_ts_ms=ts,
        block_number=block,
        block_ts=ts // 1000,
        tx_hash=f"0x{ts:064x}",
        log_index=0,
        direction=direction,
        amount_token0=n,
        amount_token1=Decimal("-1"),
        price_usdc_per_wrapper=Decimal(price) if price is not None else None,
        notional_usd=n,
    )


def _pool_at_mid(mid: Decimal, *, liquidity: int) -> AmmPoolState:
    """token0=quote(USDC 6dp), token1=base(18dp) — same scaffold as PnL v2 tests."""
    ratio = Decimal(10) ** 12 / mid
    sqrt_price_x96 = int(ratio.sqrt() * Decimal(2**96))
    return AmmPoolState(
        pool_fee=3000,
        sqrt_price_x96=sqrt_price_x96,
        liquidity=liquidity,
        token0_is_quote=True,
        token0_decimals=6,
        token1_decimals=18,
    )


def _deep_pool(*, mid: str = "100") -> AmmPoolState:
    """Liquid pool: $1k fills cleanly."""
    return _pool_at_mid(Decimal(mid), liquidity=10**24)


def _thin_pool(*, mid: str = "100") -> AmmPoolState:
    """Near-empty range — $1k unfillable under single-range safety rail."""
    return _pool_at_mid(Decimal(mid), liquidity=1)


class TestDirectionMap:
    def test_buy_fluxion(self) -> None:
        assert (
            profitable_swap_direction("buy_fluxion_sell_bybit") == "buy_native"
        )

    def test_sell_fluxion(self) -> None:
        assert (
            profitable_swap_direction("buy_bybit_sell_fluxion") == "sell_native"
        )

    def test_unknown(self) -> None:
        with pytest.raises(ValueError, match="unknown paper direction"):
            profitable_swap_direction("sideways")


class TestSwapsInWindow:
    def test_inclusive_bounds(self) -> None:
        swaps = [_swap(ts=1000), _swap(ts=2000), _swap(ts=3000)]
        got = swaps_in_window(swaps, start_ms=1000, end_ms=2000)
        assert [s.recv_ts_ms for s in got] == [1000, 2000]

    def test_rejects_inverted(self) -> None:
        with pytest.raises(ValueError, match="end_ms"):
            swaps_in_window([], start_ms=5, end_ms=1)


class TestMatching:
    def test_filters_direction(self) -> None:
        swaps = [
            _swap(ts=1, direction="buy_native"),
            _swap(ts=2, direction="sell_native"),
            _swap(ts=3, direction="unknown"),
        ]
        m = matching_profitable_swaps(
            swaps, paper_direction="buy_fluxion_sell_bybit"
        )
        assert len(m) == 1
        assert m[0].direction == "buy_native"


class TestClassify:
    def test_taken(self) -> None:
        m = classify_window(
            paper_direction="buy_fluxion_sell_bybit",
            size_usd=Decimal(1000),
            swaps_in_range=[_swap(ts=1, direction="buy_native")],
            pool=_deep_pool(),
        )
        assert m.classification == "taken"
        assert m.n_swaps_matching == 1
        assert m.depth_adequate is True

    def test_untaken_with_liquidity(self) -> None:
        m = classify_window(
            paper_direction="buy_fluxion_sell_bybit",
            size_usd=Decimal(1000),
            swaps_in_range=[_swap(ts=1, direction="sell_native")],  # wrong dir
            pool=_deep_pool(),
        )
        assert m.classification == "untaken_with_liquidity"
        assert m.n_swaps_total == 1
        assert m.n_swaps_matching == 0
        assert m.depth_adequate is True

    def test_untaken_too_thin(self) -> None:
        m = classify_window(
            paper_direction="buy_fluxion_sell_bybit",
            size_usd=Decimal(1000),
            swaps_in_range=[],
            pool=_thin_pool(),
        )
        assert m.classification == "untaken_too_thin"
        assert m.depth_adequate is False

    def test_no_pool_is_too_thin(self) -> None:
        m = classify_window(
            paper_direction="buy_bybit_sell_fluxion",
            size_usd=Decimal(1000),
            swaps_in_range=[],
            pool=None,
        )
        assert m.classification == "untaken_too_thin"
        assert m.virtual_quote_usd is None

    def test_taken_even_when_thin(self) -> None:
        # Someone filled *something* — still "taken" (validates real dislocation).
        m = classify_window(
            paper_direction="buy_fluxion_sell_bybit",
            size_usd=Decimal(1000),
            swaps_in_range=[_swap(ts=1, direction="buy_native", notional=10)],
            pool=_thin_pool(),
        )
        assert m.classification == "taken"


class TestDepth:
    def test_deep_supports_1k(self) -> None:
        assert depth_supports_size(
            _deep_pool(),
            size_usd=Decimal(1000),
            paper_direction="buy_fluxion_sell_bybit",
        )

    def test_thin_rejects_1k(self) -> None:
        assert not depth_supports_size(
            _thin_pool(),
            size_usd=Decimal(1000),
            paper_direction="buy_fluxion_sell_bybit",
        )

    def test_virtual_quote_positive(self) -> None:
        vq = virtual_quote_side_usd(_deep_pool())
        assert vq > 0


class TestStalenessAndBasis:
    def test_pool_age_stats_empty(self) -> None:
        s = pool_age_stats([])
        assert s["n"] == 0
        assert s["max_ms"] == 0

    def test_pool_age_stats(self) -> None:
        s = pool_age_stats([10, 20, 30, 40, 100])
        assert s["n"] == 5
        assert s["min_ms"] == 10
        assert s["max_ms"] == 100
        assert s["median_ms"] == 30

    def test_abs_basis(self) -> None:
        # 1% = 100 bps
        bps = abs_basis_bps(amm_mid=Decimal("101"), cex_mid=Decimal("100"))
        assert bps == Decimal(100)

    def test_abs_basis_bad_mid(self) -> None:
        assert abs_basis_bps(amm_mid=Decimal(0), cex_mid=Decimal(100)) is None


class TestNotional:
    def test_quote_leg_preferred(self) -> None:
        n = swap_notional_usd(
            amount_token0=Decimal("250.5"),
            amount_token1=Decimal("-2"),
            token0_is_quote=True,
            price_usdc_per_wrapper=Decimal(100),
        )
        assert n == Decimal("250.5")

    def test_base_times_mid_fallback(self) -> None:
        n = swap_notional_usd(
            amount_token0=Decimal(0),
            amount_token1=Decimal("-3"),
            token0_is_quote=True,
            price_usdc_per_wrapper=Decimal(50),
        )
        assert n == Decimal(150)
