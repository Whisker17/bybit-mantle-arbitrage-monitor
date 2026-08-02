"""WHI-777: DEX aggregation, ratio, truncation metadata."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from monitor.metrics.config import load_metrics_config
from monitor.metrics.volume import (
    aggregate_dex_volume,
    build_volume_compare,
    volume_ratio,
)
from monitor.quotes import CexVolumeTick, FluxionSwapTick


def _swap(
    *,
    pair_id: str = "TSLAx",
    recv_ts_ms: int,
    block_ts: int | None = None,
    amount0: str = "100",
    amount1: str = "-0.4",
    direction: str = "buy_native",
    log_index: int = 0,
) -> FluxionSwapTick:
    return FluxionSwapTick(
        pair_id=pair_id,
        pool="0x" + "11" * 20,
        block_number=1,
        block_ts=block_ts if block_ts is not None else recv_ts_ms // 1000,
        recv_ts_ms=recv_ts_ms,
        tx_hash="0x" + f"{log_index:064x}",
        log_index=log_index,
        sender="0x" + "aa" * 20,
        recipient="0x" + "bb" * 20,
        amount0=0,
        amount1=0,
        sqrt_price_x96=2**96,
        liquidity=10**18,
        tick=0,
        amount_token0=Decimal(amount0),
        amount_token1=Decimal(amount1),
        direction=direction,  # type: ignore[arg-type]
        price_usdc_per_wrapper=Decimal("250"),
    )


def test_volume_ratio_none_when_dex_zero() -> None:
    assert volume_ratio(Decimal("100"), Decimal("0")) is None
    assert volume_ratio(None, Decimal("10")) is None
    assert volume_ratio(Decimal("100"), Decimal("10")) == Decimal("10")


def test_aggregate_dex_volume_sums_quote_leg() -> None:
    metrics = load_metrics_config()
    # quote is token0 → notional = |amount0|
    swaps = [
        _swap(recv_ts_ms=1_000, amount0="100", log_index=0),
        _swap(recv_ts_ms=2_000, amount0="-50", log_index=1),
    ]
    win = aggregate_dex_volume(
        swaps,
        quote_is_token0=True,
        since_ms=0,
        now_ms=10_000,
        metrics=metrics,
        collector_started_ms=1_000,
    )
    assert win.volume_usd == Decimal("150")
    assert win.trade_count == 2
    assert win.truncated is True  # collector started after since
    assert win.window_start_ms == 1_000


def test_aggregate_truncated_with_zero_swaps_uses_collector_start() -> None:
    """Illiquid pair: no swaps still truncated when collector is young."""
    metrics = load_metrics_config()
    win = aggregate_dex_volume(
        [],
        quote_is_token0=True,
        since_ms=0,
        now_ms=10_000,
        metrics=metrics,
        collector_started_ms=9_000,
    )
    assert win.volume_usd == Decimal(0)
    assert win.trade_count == 0
    assert win.truncated is True
    assert win.window_start_ms == 9_000


def test_aggregate_not_truncated_when_full_window() -> None:
    metrics = load_metrics_config()
    swaps = [_swap(recv_ts_ms=5_000, amount0="10", log_index=0)]
    win = aggregate_dex_volume(
        swaps,
        quote_is_token0=True,
        since_ms=5_000,
        now_ms=10_000,
        metrics=metrics,
        collector_started_ms=1_000,
        earliest_recv_ts_ms=1_000,
    )
    # collector started before since → full requested window available
    assert win.truncated is False
    assert win.window_start_ms == 5_000
    assert win.volume_usd == Decimal("10")


def test_build_volume_compare_ratio() -> None:
    metrics = load_metrics_config()
    cex = CexVolumeTick(
        pair_id="TSLAx",
        symbol="TSLAXUSDT",
        poll_ts_ms=9_000,
        recv_ts_ms=9_001,
        volume_quote_24h=Decimal("1000"),
        trade_count_24h=None,
        source="bybit",
    )
    swaps = [_swap(recv_ts_ms=8_000, amount0="100", log_index=0)]
    cmp_ = build_volume_compare(
        cex_tick=cex,
        swaps=swaps,
        quote_is_token0=True,
        since_ms=0,
        now_ms=10_000,
        metrics=metrics,
        earliest_swap_recv_ts_ms=8_000,
    )
    assert cmp_.cex_volume_24h == Decimal("1000")
    assert cmp_.dex.volume_usd == Decimal("100")
    assert cmp_.volume_ratio == Decimal("10")
    assert cmp_.dex.truncated is True


def test_store_and_reader_cex_volume(tmp_path: Path) -> None:
    from monitor.storage import JournalReader, SqliteStore

    db = tmp_path / "v.db"
    store = SqliteStore(db)
    tick = CexVolumeTick(
        pair_id="TSLAx",
        symbol="TSLAXUSDT",
        poll_ts_ms=100,
        recv_ts_ms=101,
        volume_quote_24h=Decimal("42.5"),
        trade_count_24h=7,
        source="bybit",
    )
    assert store.insert_cex_volume([tick]) == 1
    # newer overwrites latest
    tick2 = CexVolumeTick(
        pair_id="TSLAx",
        symbol="TSLAXUSDT",
        poll_ts_ms=200,
        recv_ts_ms=201,
        volume_quote_24h=Decimal("99"),
        trade_count_24h=None,
        source="bybit",
    )
    store.insert_cex_volume([tick2])
    store.close()

    with JournalReader(db) as r:
        latest = r.latest_cex_volume("TSLAx")
        assert latest is not None
        assert latest.volume_quote_24h == Decimal("99")
        assert latest.trade_count_24h is None
        assert r.latest_cex_volume("NOPE") is None
