"""Seam: JournalReader health helpers + /api/health response shape."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from monitor.api.health import HealthStatus, build_health
from monitor.quotes import BybitBookTick, CollectorGap, now_ms
from monitor.storage import JournalReader, SqliteStore


def _seed_book(store: SqliteStore, *, ts: int, pair_id: str = "AAPLx") -> None:
    store.insert_bybit_book(
        [
            BybitBookTick(
                pair_id=pair_id,
                symbol=f"{pair_id.upper()}USDT",
                exchange_ts_ms=ts,
                recv_ts_ms=ts,
                bid=Decimal("100"),
                ask=Decimal("100.2"),
                bid_de_multiplied=Decimal("100"),
                ask_de_multiplied=Decimal("100.2"),
                multiplier=Decimal(1),
            )
        ]
    )


def test_reader_meta_and_freshest(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    store = SqliteStore(db)
    store.set_meta("last_block", "42")
    store.set_meta("collector_started_ms", "1000")
    _seed_book(store, ts=5_000)
    store.close()

    with JournalReader(db) as reader:
        assert reader.get_meta("last_block") == "42"
        assert reader.get_meta("missing") is None
        assert reader.freshest_recv_ts_ms() == 5_000


def test_reader_recent_gaps(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    store = SqliteStore(db)
    store.insert_gap(
        CollectorGap(
            source="mantle",
            gap_start_ms=1_000,
            gap_end_ms=2_000,
            detail="lag",
        )
    )
    store.insert_gap(
        CollectorGap(
            source="bybit",
            gap_start_ms=9_000,
            gap_end_ms=10_000,
            detail="reconnect",
        )
    )
    store.close()

    with JournalReader(db) as reader:
        gaps = reader.recent_gaps(since_ms=5_000, limit=10)
        assert len(gaps) == 1
        assert gaps[0].source == "bybit"
        assert gaps[0].detail == "reconnect"


def test_reader_recent_gaps_source_filter_before_limit(tmp_path: Path) -> None:
    """WHI-825: source filter must apply before LIMIT so spam cannot crowd out."""
    db = tmp_path / "m.db"
    store = SqliteStore(db)
    # Many recent non-down gaps + one older collector_down.
    for i in range(20):
        store.insert_gap(
            CollectorGap(
                source="mantle_blocks",
                gap_start_ms=10_000 + i,
                gap_end_ms=20_000 + i,
                detail="lag spam",
            )
        )
    store.insert_gap(
        CollectorGap(
            source="collector_down",
            gap_start_ms=1_000,
            gap_end_ms=5_000,
            detail="downtime",
        )
    )
    store.close()

    with JournalReader(db) as reader:
        # Without source filter, limit=5 would miss collector_down.
        spam = reader.recent_gaps(since_ms=0, limit=5)
        assert all(g.source == "mantle_blocks" for g in spam)
        downs = reader.recent_gaps(since_ms=0, limit=5, source="collector_down")
        assert len(downs) == 1
        assert downs[0].source == "collector_down"
        assert downs[0].detail == "downtime"


def test_build_health_alive(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    store = SqliteStore(db)
    ts = now_ms()
    store.set_meta("collector_started_ms", str(ts - 60_000))
    store.set_meta("last_block", "99")
    store.set_meta("last_block_ingest_latency_ms", "12.5")
    _seed_book(store, ts=ts - 1_000)
    store.close()

    with JournalReader(db) as reader:
        health = build_health(
            reader,
            now=ts,
            stale_ms=30_000,
            gap_window_ms=300_000,
        )
    assert health.db_exists is True
    assert health.collector_alive is True
    assert health.last_block == 99
    assert health.last_block_ingest_latency_ms == 12.5
    assert health.ok is True


def test_build_health_uses_last_tick_meta_without_scan(tmp_path: Path) -> None:
    """WHI-825: O(1) collector_last_tick_write_ms beats multi-table MAX scan."""
    from monitor.collector.watchdog import META_HEARTBEAT, META_LAST_TICK_WRITE

    db = tmp_path / "m.db"
    store = SqliteStore(db)
    ts = now_ms()
    store.set_meta("collector_started_ms", str(ts - 60_000))
    store.set_meta(META_HEARTBEAT, str(ts - 500))
    # Stale book row would look "down" if scanned; meta is the source of truth.
    _seed_book(store, ts=ts - 3_600_000)
    store.set_meta(META_LAST_TICK_WRITE, str(ts - 2_000))
    store.close()

    with JournalReader(db) as reader:
        health = build_health(
            reader,
            now=ts,
            stale_ms=30_000,
            gap_window_ms=300_000,
            quiet_ms=60_000,
        )
    assert health.collector_alive is True
    assert health.freshest_recv_ts_ms == ts - 2_000
    assert health.age_ms is not None and health.age_ms < 5_000
    assert health.feed_state == "ok"


def test_build_health_stale_collector(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    store = SqliteStore(db)
    store.set_meta("collector_started_ms", "1")
    _seed_book(store, ts=1_000)  # ancient
    store.close()

    with JournalReader(db) as reader:
        health = build_health(
            reader,
            now=1_000_000,
            stale_ms=30_000,
            gap_window_ms=300_000,
        )
    assert health.collector_alive is False
    assert health.ok is False


def test_unavailable_health() -> None:
    h = HealthStatus.unavailable(db_path="/tmp/x.db", now=1, error="missing")
    assert h.ok is False
    assert h.db_exists is False
    assert h.error == "missing"
    assert h.uncovered_coverage_mismatches == []
    assert h.uncovered_coverage_probe_ms is None
    assert h.uncovered_coverage_probe_errors == []
    assert h.unpublished_pyth_feeds == []


def test_build_health_exposes_uncovered_mismatches(tmp_path: Path) -> None:
    """WHI-787: journal meta for stale uncovered flags surfaces on /api/health."""
    from monitor.underlying.coverage_probe import (
        META_MISMATCHES,
        META_PROBE_ERRORS,
        META_PROBE_MS,
        ProbeError,
        UncoveredMismatch,
        errors_to_meta_json,
        mismatches_to_meta_json,
    )

    db = tmp_path / "m.db"
    store = SqliteStore(db)
    ts = now_ms()
    store.set_meta("collector_started_ms", str(ts - 60_000))
    _seed_book(store, ts=ts - 1_000)
    store.set_meta(
        META_MISMATCHES,
        mismatches_to_meta_json(
            [
                UncoveredMismatch(
                    ticker="SPCX",
                    sources=("yahoo",),
                    detail="Yahoo chart returned a positive last price",
                )
            ]
        ),
    )
    store.set_meta(
        META_PROBE_ERRORS,
        errors_to_meta_json(
            [ProbeError(ticker="GHOST", source="yahoo", error="timeout")]
        ),
    )
    store.set_meta(META_PROBE_MS, "1_700_000_000_000".replace("_", ""))
    store.close()

    with JournalReader(db) as reader:
        health = build_health(
            reader,
            now=ts,
            stale_ms=30_000,
            gap_window_ms=300_000,
        )
    assert health.ok is True  # mismatches are advisory, not ok-flip
    assert health.uncovered_coverage_probe_ms == 1_700_000_000_000
    assert len(health.uncovered_coverage_mismatches) == 1
    assert health.uncovered_coverage_mismatches[0]["ticker"] == "SPCX"
    assert health.uncovered_coverage_mismatches[0]["sources"] == ["yahoo"]
    assert health.uncovered_coverage_probe_errors == [
        {"ticker": "GHOST", "source": "yahoo", "error": "timeout"}
    ]
    assert health.unpublished_pyth_feeds == []


def test_build_health_exposes_unpublished_pyth_feeds(tmp_path: Path) -> None:
    """WHI-794: journal meta for never-published Hermes feeds on /api/health."""
    from monitor.underlying.coverage_probe import (
        META_UNPUBLISHED,
        UnpublishedFeed,
        unpublished_to_meta_json,
    )

    db = tmp_path / "m.db"
    store = SqliteStore(db)
    ts = now_ms()
    store.set_meta("collector_started_ms", str(ts - 60_000))
    _seed_book(store, ts=ts - 1_000)
    store.set_meta(
        META_UNPUBLISHED,
        unpublished_to_meta_json(
            [
                UnpublishedFeed(
                    ticker="SOXL",
                    feed_id="53008e9cb71d",
                    publish_time=0,
                    price="0",
                    detail="never published",
                )
            ]
        ),
    )
    store.close()

    with JournalReader(db) as reader:
        health = build_health(
            reader,
            now=ts,
            stale_ms=30_000,
            gap_window_ms=300_000,
        )
    assert health.ok is True
    assert len(health.unpublished_pyth_feeds) == 1
    assert health.unpublished_pyth_feeds[0]["ticker"] == "SOXL"
    assert health.unpublished_pyth_feeds[0]["publish_time"] == 0


def test_build_health_feed_state_down(tmp_path: Path) -> None:
    """WHI-825: stale data without heartbeat → feed_down."""
    db = tmp_path / "m.db"
    store = SqliteStore(db)
    store.set_meta("collector_started_ms", "1")
    _seed_book(store, ts=1_000)
    store.close()

    with JournalReader(db) as reader:
        health = build_health(
            reader,
            now=1_000_000,
            stale_ms=30_000,
            gap_window_ms=300_000,
            market_id="binance-pancake",
        )
    assert health.collector_alive is False
    assert health.feed_state == "feed_down"
    assert health.recovery_hint is not None
    assert "binance-pancake" in health.recovery_hint
    assert "systemctl" in health.recovery_hint


def test_build_health_feed_state_gap(tmp_path: Path) -> None:
    """WHI-825: alive process + recent collector_down → feed_state gap."""
    from monitor.collector.watchdog import META_HEARTBEAT

    db = tmp_path / "m.db"
    store = SqliteStore(db)
    ts = now_ms()
    store.set_meta("collector_started_ms", str(ts - 60_000))
    store.set_meta(META_HEARTBEAT, str(ts - 1_000))
    _seed_book(store, ts=ts - 1_000)
    store.insert_gap(
        CollectorGap(
            source="collector_down",
            gap_start_ms=ts - 3_600_000,
            gap_end_ms=ts - 60_000,
            detail="market=binance-pancake collector process down duration_ms=3540000",
        )
    )
    store.close()

    with JournalReader(db) as reader:
        health = build_health(
            reader,
            now=ts,
            stale_ms=30_000,
            gap_window_ms=24 * 3_600_000,
            market_id="binance-pancake",
        )
    assert health.collector_alive is True
    assert health.collector_down_gap_recent is True
    assert health.feed_state == "gap"
    assert health.recovery_hint is not None
    assert "downtime" in health.recovery_hint.lower() or "hole" in health.recovery_hint


def test_build_health_gap_survives_block_lag_spam(tmp_path: Path) -> None:
    """collector_down must not be crowded out of feed_state by other gap sources."""
    from monitor.collector.watchdog import META_HEARTBEAT

    db = tmp_path / "m.db"
    store = SqliteStore(db)
    ts = now_ms()
    store.set_meta("collector_started_ms", str(ts - 60_000))
    store.set_meta(META_HEARTBEAT, str(ts - 1_000))
    _seed_book(store, ts=ts - 1_000)
    for i in range(30):
        store.insert_gap(
            CollectorGap(
                source="mantle_blocks",
                gap_start_ms=ts - 10_000 + i,
                gap_end_ms=ts - 9_000 + i,
                detail="lag spam",
            )
        )
    store.insert_gap(
        CollectorGap(
            source="collector_down",
            gap_start_ms=ts - 3_600_000,
            gap_end_ms=ts - 120_000,
            detail="market=binance-pancake collector process down duration_ms=3480000",
        )
    )
    store.close()

    with JournalReader(db) as reader:
        health = build_health(
            reader,
            now=ts,
            stale_ms=30_000,
            gap_window_ms=24 * 3_600_000,
            market_id="binance-pancake",
        )
    assert health.collector_down_gap_recent is True
    assert health.feed_state == "gap"


def test_build_health_prefers_heartbeat_over_quiet_ticks(tmp_path: Path) -> None:
    """Heartbeat keeps collector_alive when tick tables are quiet (closed session)."""
    from monitor.collector.watchdog import META_HEARTBEAT

    db = tmp_path / "m.db"
    store = SqliteStore(db)
    ts = now_ms()
    store.set_meta("collector_started_ms", str(ts - 600_000))
    store.set_meta(META_HEARTBEAT, str(ts - 5_000))
    # Tick data 2 minutes old — quiet market, process still heartbeating.
    _seed_book(store, ts=ts - 120_000)
    store.close()

    with JournalReader(db) as reader:
        health = build_health(
            reader,
            now=ts,
            stale_ms=30_000,
            gap_window_ms=300_000,
            quiet_ms=60_000,
        )
    assert health.collector_alive is True
    assert health.feed_state == "feed_quiet"
    assert health.age_ms is not None and health.age_ms >= 100_000
