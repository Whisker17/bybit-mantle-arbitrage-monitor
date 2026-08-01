"""Seams: retention prune, 1m downsample, disk waterline, growth snapshot."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from monitor.collector.config import (
    DiskGuardConfig,
    RetentionConfig,
    load_collector_config,
)
from monitor.quotes import (
    BybitBookTick,
    BybitTradeTick,
    CollectorGap,
    FluxionPoolStateTick,
    FluxionRfqFillTick,
    FluxionRfqQuoteTick,
    FluxionSwapTick,
)
from monitor.storage import JournalReader, SqliteStore
from monitor.storage.retention import (
    classify_disk,
    effective_ttls,
    format_growth_report,
    growth_snapshot,
)
from monitor.storage.schema import SCHEMA_VERSION


def _book(pair: str, ts: int, bid: str = "100") -> BybitBookTick:
    return BybitBookTick(
        pair_id=pair,
        symbol=f"{pair.upper()}USDT",
        exchange_ts_ms=ts,
        recv_ts_ms=ts + 1,
        bid=Decimal(bid),
        ask=Decimal(bid) + Decimal("0.1"),
        bid_de_multiplied=Decimal(bid),
        ask_de_multiplied=Decimal(bid) + Decimal("0.1"),
        multiplier=Decimal(1),
    )


def _policy(**overrides: object) -> RetentionConfig:
    base = RetentionConfig(
        enabled=True,
        interval_s=60,
        bybit_book_raw_ms=1_000,
        # Keep bars long enough that seeds at now-90s still survive prune.
        bybit_book_1m_ms=86_400_000,
        bybit_trades_ms=1_000,
        fluxion_pool_state_ms=1_000,
        fluxion_rfq_quotes_ms=1_000,
        fluxion_swaps_ms=None,
        fluxion_rfq_fills_ms=None,
        collector_gaps_ms=1_000,
        delete_batch_size=100,
        incremental_vacuum_pages=0,
        full_vacuum=False,
        disk=DiskGuardConfig(
            path=None,
            warn_free_bytes=10_000,
            critical_free_bytes=1_000,
            warn_ttl_factor=0.25,
            critical_ttl_factor=0.05,
            pause_book_writes_on_critical=True,
        ),
    )
    if not overrides:
        return base
    return base.model_copy(update=overrides)


def test_checked_in_collector_includes_retention() -> None:
    cfg = load_collector_config()
    assert cfg.retention.enabled is True
    assert cfg.retention.bybit_book_raw_ms == 172_800_000
    assert cfg.retention.fluxion_swaps_ms is None
    assert cfg.retention.disk.critical_free_bytes == 1_073_741_824


def test_classify_disk_levels() -> None:
    disk = DiskGuardConfig(
        warn_free_bytes=2000,
        critical_free_bytes=1000,
        warn_ttl_factor=0.5,
        critical_ttl_factor=0.1,
    )
    assert classify_disk(5000, disk) == "ok"
    assert classify_disk(1500, disk) == "warn"
    assert classify_disk(500, disk) == "critical"


def test_effective_ttls_accelerate_on_warn_not_swaps() -> None:
    cfg = _policy(bybit_book_raw_ms=10_000_000, fluxion_swaps_ms=None)
    warn = effective_ttls(cfg, "warn")
    assert warn.bybit_book_raw_ms == 2_500_000  # 0.25 factor
    assert warn.fluxion_swaps_ms is None
    crit = effective_ttls(cfg, "critical")
    assert crit.bybit_book_raw_ms == 500_000  # 0.05 factor
    # Floor: tiny TTLs never go below 60s.
    tiny = effective_ttls(_policy(bybit_book_raw_ms=100_000), "critical")
    assert tiny.bybit_book_raw_ms == 60_000


def test_prune_books_materializes_1m_and_keeps_swaps(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    store = SqliteStore(db)
    assert store.get_meta("schema_version") == str(SCHEMA_VERSION)

    now = 1_700_000_100_000
    # Two minutes of books older than raw TTL (1000 ms) — will be pruned.
    old_a = now - 60_000
    old_b = now - 90_000
    store.insert_bybit_book(
        [
            _book("TSLAx", old_b, "10"),
            _book("TSLAx", old_b + 100, "11"),  # same minute bucket
            _book("TSLAx", old_a, "20"),
            _book("TSLAx", now - 100, "30"),  # recent — keep
        ]
    )
    store.insert_bybit_trades(
        [
            BybitTradeTick(
                pair_id="TSLAx",
                symbol="TSLAXUSDT",
                exchange_ts_ms=old_b,
                recv_ts_ms=old_b + 1,
                trade_id="t-old",
                price=Decimal("10"),
                price_de_multiplied=Decimal("10"),
                size=Decimal("1"),
                side="Buy",
                multiplier=Decimal(1),
            ),
            BybitTradeTick(
                pair_id="TSLAx",
                symbol="TSLAXUSDT",
                exchange_ts_ms=now - 50,
                recv_ts_ms=now - 49,
                trade_id="t-new",
                price=Decimal("30"),
                price_de_multiplied=Decimal("30"),
                size=Decimal("2"),
                side="Sell",
                multiplier=Decimal(1),
            ),
        ]
    )
    swap = FluxionSwapTick(
        pair_id="TSLAx",
        pool="0x" + "11" * 20,
        block_number=1,
        block_ts=old_b // 1000,
        recv_ts_ms=old_b,
        tx_hash="0x" + "ab" * 32,
        log_index=0,
        sender="0x" + "aa" * 20,
        recipient="0x" + "bb" * 20,
        amount0=1,
        amount1=-1,
        sqrt_price_x96=2**96,
        liquidity=1,
        tick=0,
        amount_token0=Decimal("1"),
        amount_token1=Decimal("-1"),
        direction="sell_native",
        price_usdc_per_wrapper=Decimal("10"),
    )
    store.insert_swaps([swap])
    store.insert_rfq_fills(
        [
            FluxionRfqFillTick(
                block_number=1,
                block_ts=old_b // 1000,
                recv_ts_ms=old_b,
                tx_hash="0x" + "cd" * 32,
                log_index=0,
                order_hash="0x" + "ef" * 32,
                remaining_making_amount=0,
            )
        ]
    )
    store.insert_pool_state(
        [
            FluxionPoolStateTick(
                pair_id="TSLAx",
                pool="0x" + "11" * 20,
                block_number=1,
                block_ts=old_b // 1000,
                recv_ts_ms=old_b,
                sqrt_price_x96=2**96,
                tick=0,
                liquidity=1,
                token0="0x" + "22" * 20,
                token1="0x" + "33" * 20,
                mid_usdc_per_wrapper=Decimal("10"),
                mid_usdc_per_native=Decimal("10"),
                wrapper_assets_per_share=Decimal(1),
            ),
            FluxionPoolStateTick(
                pair_id="TSLAx",
                pool="0x" + "11" * 20,
                block_number=2,
                block_ts=(now - 50) // 1000,
                recv_ts_ms=now - 50,
                sqrt_price_x96=2**96,
                tick=0,
                liquidity=1,
                token0="0x" + "22" * 20,
                token1="0x" + "33" * 20,
                mid_usdc_per_wrapper=Decimal("30"),
                mid_usdc_per_native=Decimal("30"),
                wrapper_assets_per_share=Decimal(1),
            ),
        ]
    )
    store.insert_rfq_quotes(
        [
            FluxionRfqQuoteTick(
                pair_id="TSLAx",
                poll_ts_ms=old_b,
                recv_ts_ms=old_b + 1,
                token_in="0xusdc",
                token_out="0xtsla",
                amount_in="1",
                amount_out="1",
                price=Decimal("10"),
                side="buy",
                request_id="r1",
                http_status=200,
                available=True,
            ),
            FluxionRfqQuoteTick(
                pair_id="TSLAx",
                poll_ts_ms=now - 50,
                recv_ts_ms=now - 49,
                token_in="0xusdc",
                token_out="0xtsla",
                amount_in="1",
                amount_out="1",
                price=Decimal("30"),
                side="buy",
                request_id="r2",
                http_status=200,
                available=True,
            ),
        ]
    )
    store.insert_gap(
        CollectorGap(
            source="test",
            gap_start_ms=old_b,
            gap_end_ms=old_b + 1,
            detail="old",
        )
    )

    report = store.run_retention(_policy(), now_ms=now, free_bytes=100_000)
    assert report.disk_level == "ok"
    assert report.book_writes_paused is False
    assert report.deleted.get("bybit_book", 0) == 3
    assert report.bars_upserted >= 2
    assert store.count("bybit_book") == 1
    assert store.count("bybit_book_1m") >= 2
    assert store.count("bybit_trades") == 1
    assert store.count("fluxion_swaps") == 1  # permanent
    assert store.count("fluxion_rfq_fills") == 1
    assert store.count("fluxion_pool_state") == 1
    assert store.count("fluxion_rfq_quotes") == 1
    assert store.get_meta("retention_last_run_ms") == str(now)

    # Latest tick + rolling volume still readable; permanent swap survives.
    with JournalReader(db) as reader:
        latest = reader.latest_bybit_book("TSLAx")
        assert latest is not None
        assert latest.bid_de_multiplied == Decimal("30")
        vol = reader.volume_stats("TSLAx", since_ms=now - 500)
        assert vol.bybit_trade_count == 1
        # Old permanent swap is outside the short window but still in the journal.
        all_vol = reader.volume_stats("TSLAx", since_ms=0)
        assert all_vol.fluxion_swap_count == 1
        pool = reader.latest_pool_state("TSLAx")
        assert pool is not None
        assert pool.mid_usdc_per_native == Decimal("30")
        rfq = reader.latest_rfq_quote("TSLAx", leg="buy")
        assert rfq is not None
        assert rfq.price == Decimal("30")

    # 1m bar holds last tick of the older minute.
    row = store._conn.execute(  # noqa: SLF001
        "SELECT bid_de_multiplied, n FROM bybit_book_1m "
        "WHERE pair_id=? ORDER BY bucket_ts_ms ASC LIMIT 1",
        ("TSLAx",),
    ).fetchone()
    assert row is not None
    assert str(row["bid_de_multiplied"]) == "11"  # last of first minute
    assert int(row["n"]) == 2
    store.close()


def test_disk_critical_pauses_book_writes_flag(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    store = SqliteStore(db)
    now = 1_700_000_000_000
    store.insert_bybit_book([_book("AAPLx", now - 50_000)])
    report = store.run_retention(_policy(), now_ms=now, free_bytes=100)
    assert report.disk_level == "critical"
    assert report.book_writes_paused is True
    store.close()


def test_growth_snapshot_and_format(tmp_path: Path) -> None:
    db = tmp_path / "g.db"
    store = SqliteStore(db)
    t0 = 1_700_000_000_000
    store.insert_bybit_book(
        [_book("TSLAx", t0), _book("TSLAx", t0 + 86_400_000)]
    )
    snap = growth_snapshot(store._conn, db_path=db)  # noqa: SLF001
    book = next(t for t in snap.tables if t.table == "bybit_book")
    assert book.rows == 2
    assert book.rows_per_day is not None
    assert book.rows_per_day == 2.0  # two rows over exactly 1 day
    text = format_growth_report(snap)
    assert "bybit_book" in text
    assert "rows/day" in text
    store.close()


def test_batch_delete_respects_batch_size(tmp_path: Path) -> None:
    db = tmp_path / "b.db"
    store = SqliteStore(db)
    now = 2_000_000_000_000
    ticks = [_book("NVTSx", now - 10_000 - i) for i in range(250)]
    ticks.append(_book("NVTSx", now - 10))  # keep
    store.insert_bybit_book(ticks)
    report = store.run_retention(
        _policy(delete_batch_size=50, bybit_book_raw_ms=1_000),
        now_ms=now,
        free_bytes=10**12,
    )
    assert report.deleted["bybit_book"] == 250
    assert store.count("bybit_book") == 1
    store.close()
