"""Seam: pool quotability — residual V3 slot0 mid is not tradable when empty.

WHI-795: empty pools (liquidity==0) still carry last-trade sqrtPriceX96;
downstream bps must be n/a with reason ``empty_pool``, not phantom ±1000s bps.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from monitor.metrics.amm_quote import quotable_amm_mid
from monitor.metrics.config import load_metrics_config
from monitor.metrics.snapshot import build_edge_snapshot, build_spread_snapshot
from monitor.quotes import BybitBookTick, FluxionPoolStateTick

# Test-only scaffold (not production) — residual empty-pool mids often exceed this.
MAX_SANE_ABS_BPS = Decimal(5000)


def assert_sane_bps(value: Decimal | None, *, limit: Decimal = MAX_SANE_ABS_BPS) -> None:
    if value is None:
        return
    if abs(value) > limit:
        raise AssertionError(
            f"bps magnitude {value} exceeds sane limit ±{limit} "
            f"(likely residual empty-pool mid or bad join)"
        )


ET = ZoneInfo("America/New_York")


def _open_ts_ms() -> int:
    return int(datetime(2026, 8, 3, 10, 0, tzinfo=ET).timestamp() * 1000)


def _bybit(*, mid: Decimal = Decimal("60.72"), ts_ms: int | None = None) -> BybitBookTick:
    """L1 around ``mid`` (half-spread 0.10 like live books)."""
    t = ts_ms if ts_ms is not None else _open_ts_ms()
    half = Decimal("0.05")
    return BybitBookTick(
        pair_id="CRCLB",
        symbol="CRCLBUSDT",
        exchange_ts_ms=t,
        recv_ts_ms=t,
        bid=mid - half,
        ask=mid + half,
        bid_de_multiplied=mid - half,
        ask_de_multiplied=mid + half,
        multiplier=Decimal(1),
    )


def _pool(
    *,
    pair_id: str = "CRCLB",
    mid: Decimal = Decimal("66.888882385"),
    liquidity: int = 0,
    ts_ms: int | None = None,
) -> FluxionPoolStateTick:
    t = ts_ms if ts_ms is not None else _open_ts_ms()
    return FluxionPoolStateTick(
        pair_id=pair_id,
        pool="0x" + "11" * 20,
        block_number=113_617_512,
        block_ts=t // 1000,
        recv_ts_ms=t,
        sqrt_price_x96=2**96,
        tick=0,
        liquidity=liquidity,
        token0="0x" + "22" * 20,
        token1="0x" + "33" * 20,
        mid_usdc_per_wrapper=mid,
        mid_usdc_per_native=mid,
        wrapper_assets_per_share=Decimal(1),
    )


def test_quotable_amm_mid_none_tick() -> None:
    mid, reason = quotable_amm_mid(None)
    assert mid is None
    assert reason is None


def test_quotable_amm_mid_empty_pool() -> None:
    mid, reason = quotable_amm_mid(_pool(liquidity=0, mid=Decimal("66.89")))
    assert mid is None
    assert reason == "empty_pool"


def test_quotable_amm_mid_invalid_nonpositive() -> None:
    mid, reason = quotable_amm_mid(_pool(liquidity=10**18, mid=Decimal(0)))
    assert mid is None
    assert reason == "invalid_mid"


def test_quotable_amm_mid_ok() -> None:
    mid, reason = quotable_amm_mid(_pool(liquidity=10**18, mid=Decimal("99.5")))
    assert mid == Decimal("99.5")
    assert reason is None


def test_empty_pool_spread_is_na_not_phantom_bps() -> None:
    """CRCLB-shaped residual mid must not yield +1016 bps vs CEX."""
    cfg = load_metrics_config()
    ts = _open_ts_ms()
    # Residual AMM mid 66.89 vs CEX 60.72 → ~+1016 bps if naively quoted.
    residual = Decimal("66.888882385")
    bybit = _bybit(mid=Decimal("60.72"), ts_ms=ts)
    amm = _pool(mid=residual, liquidity=0, ts_ms=ts)
    snap = build_spread_snapshot(bybit=bybit, amm=amm, config=cfg, ts_ms=ts)
    assert snap.amm_quote_reason == "empty_pool"
    # Residual mid without the gate would be large; with the gate mid is None.
    from monitor.metrics.edge import mid_from_bid_ask, spread_bps

    phantom = spread_bps(
        mid_from_bid_ask(bybit.bid_de_multiplied, bybit.ask_de_multiplied),
        residual,
    )
    assert abs(phantom) > Decimal(500)
    if snap.amm_spread_bps is not None:
        # Regression path: residual mid leaked into bps — magnitude guardrail.
        assert_sane_bps(snap.amm_spread_bps)
        raise AssertionError("empty pool must not produce amm_spread_bps")
    assert snap.amm_mid is None
    assert snap.amm_spread_bps is None


def test_empty_pool_korub_shaped_minus_100pct_suppressed() -> None:
    """Near-zero residual mid must not yield −10000 bps."""
    cfg = load_metrics_config()
    ts = _open_ts_ms()
    bybit = _bybit(mid=Decimal("15.575"), ts_ms=ts)
    residual = Decimal("0.0001")
    # After de-multiply display can look ~0; residual native mid still non-zero.
    amm = _pool(
        pair_id="KORUB",
        mid=residual,
        liquidity=0,
        ts_ms=ts,
    )
    snap = build_spread_snapshot(bybit=bybit, amm=amm, config=cfg, ts_ms=ts)
    assert snap.amm_quote_reason == "empty_pool"
    from monitor.metrics.edge import mid_from_bid_ask, spread_bps

    phantom = spread_bps(
        mid_from_bid_ask(bybit.bid_de_multiplied, bybit.ask_de_multiplied),
        residual,
    )
    # ~−100% → −10000 bps trips the magnitude guardrail.
    with pytest.raises(AssertionError):
        assert_sane_bps(phantom)
    if snap.amm_spread_bps is not None:
        assert_sane_bps(snap.amm_spread_bps)
        raise AssertionError("empty pool must not produce amm_spread_bps")
    assert snap.amm_mid is None
    assert snap.amm_spread_bps is None


def test_empty_pool_edge_snapshot_skips_amm_edges() -> None:
    cfg = load_metrics_config()
    ts = _open_ts_ms()
    from monitor.metrics.amm_pool import AmmPoolState

    amm = _pool(liquidity=0, mid=Decimal("66.89"), ts_ms=ts)
    pool = AmmPoolState(
        pool_fee=2500,
        sqrt_price_x96=amm.sqrt_price_x96,
        liquidity=0,
        token0_is_quote=True,
        token0_decimals=18,
        token1_decimals=18,
    )
    snap = build_edge_snapshot(
        bybit=_bybit(ts_ms=ts),
        amm=amm,
        amm_pool=pool,
        config=cfg,
        ts_ms=ts,
    )
    assert snap.spreads.amm_mid is None
    assert snap.spreads.amm_quote_reason == "empty_pool"
    assert snap.amm_edges == []


def test_liquid_pool_still_quotes() -> None:
    cfg = load_metrics_config()
    ts = _open_ts_ms()
    amm = _pool(liquidity=10**18, mid=Decimal("99.5"), ts_ms=ts)
    bybit = _bybit(mid=Decimal("100.10"), ts_ms=ts)
    # Rebuild bybit with exact mid 100.10 for predictable spread.
    bybit = BybitBookTick(
        pair_id="CRCLB",
        symbol="CRCLBUSDT",
        exchange_ts_ms=ts,
        recv_ts_ms=ts,
        bid=Decimal("100"),
        ask=Decimal("100.20"),
        bid_de_multiplied=Decimal("100"),
        ask_de_multiplied=Decimal("100.20"),
        multiplier=Decimal(1),
    )
    snap = build_spread_snapshot(bybit=bybit, amm=amm, config=cfg, ts_ms=ts)
    assert snap.amm_mid == Decimal("99.5")
    assert snap.amm_quote_reason is None
    assert snap.amm_spread_bps is not None
    assert_sane_bps(snap.amm_spread_bps)


def test_assert_sane_bps_rejects_phantom_magnitude() -> None:
    # Limit is ±5000 (issue WHI-795); −10000 (KORUB residual) trips, mild bps do not.
    with pytest.raises(AssertionError):
        assert_sane_bps(Decimal("5000.1"))
    with pytest.raises(AssertionError):
        assert_sane_bps(Decimal("-10000"))
    assert_sane_bps(None)
    assert_sane_bps(Decimal("120.5"))
    assert_sane_bps(Decimal("1016"))  # real-ish magnitude; empty-pool gate is the fix
    assert_sane_bps(MAX_SANE_ABS_BPS)


# ---------------------------------------------------------------------------
# WHI-822: |vs CEX| magnitude guard — liquid pool, not empty-pool residual
# ---------------------------------------------------------------------------


def test_annotate_pricing_anomaly_spyb_shaped() -> None:
    """SPYB-shaped: liquid AMM ~11% rich vs CEX → pricing_anomaly (mid kept)."""
    from monitor.metrics.amm_quote import annotate_pricing_anomaly

    mid, reason = annotate_pricing_anomaly(
        Decimal("836.50"),
        None,
        cex_mid=Decimal("751.71"),
        max_abs_spread_bps=Decimal(500),
    )
    assert mid == Decimal("836.50")
    assert reason == "pricing_anomaly"


def test_annotate_pricing_anomaly_under_threshold_ok() -> None:
    from monitor.metrics.amm_quote import annotate_pricing_anomaly

    mid, reason = annotate_pricing_anomaly(
        Decimal("692.51"),
        None,
        cex_mid=Decimal("692.31"),
        max_abs_spread_bps=Decimal(500),
    )
    assert mid == Decimal("692.51")
    assert reason is None


def test_annotate_pricing_anomaly_preserves_empty_pool() -> None:
    from monitor.metrics.amm_quote import annotate_pricing_anomaly

    mid, reason = annotate_pricing_anomaly(
        None,
        "empty_pool",
        cex_mid=Decimal("100"),
        max_abs_spread_bps=Decimal(500),
    )
    assert mid is None
    assert reason == "empty_pool"


def test_annotate_pricing_anomaly_disabled_when_threshold_none() -> None:
    from monitor.metrics.amm_quote import annotate_pricing_anomaly

    mid, reason = annotate_pricing_anomaly(
        Decimal("836.50"),
        None,
        cex_mid=Decimal("751.71"),
        max_abs_spread_bps=None,
    )
    assert mid == Decimal("836.50")
    assert reason is None


def test_spyb_shaped_spread_marks_pricing_anomaly_keeps_mid() -> None:
    """Live-shaped SPYB: L>0, huge basis — mid/spread visible, reason set, no edges."""
    cfg = load_metrics_config()
    assert cfg.max_abs_amm_spread_bps == Decimal(500)
    ts = _open_ts_ms()
    # Issue sample: CEX 751.71, AMM 836.50 → +1127.9 bps
    bybit = BybitBookTick(
        pair_id="SPYB",
        symbol="SPYBUSDT",
        exchange_ts_ms=ts,
        recv_ts_ms=ts,
        bid=Decimal("751.66"),
        ask=Decimal("751.76"),
        bid_de_multiplied=Decimal("751.66"),
        ask_de_multiplied=Decimal("751.76"),
        multiplier=Decimal(1),
    )
    amm = _pool(
        pair_id="SPYB",
        mid=Decimal("836.50"),
        liquidity=10**18,
        ts_ms=ts,
    )
    from monitor.metrics.amm_pool import AmmPoolState

    pool = AmmPoolState(
        pool_fee=100,
        sqrt_price_x96=amm.sqrt_price_x96,
        liquidity=amm.liquidity,
        token0_is_quote=True,
        token0_decimals=18,
        token1_decimals=18,
    )
    edge = build_edge_snapshot(
        bybit=bybit, amm=amm, amm_pool=pool, config=cfg, ts_ms=ts
    )
    snap = edge.spreads
    assert snap.amm_mid == Decimal("836.50")
    assert snap.amm_quote_reason == "pricing_anomaly"
    assert snap.amm_spread_bps is not None
    assert snap.amm_spread_bps > Decimal(500)
    # Tradable paper edge must not claim a fillable opportunity.
    assert edge.amm_edges == []
