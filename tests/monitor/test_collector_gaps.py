"""Seam: collector_down gap accounting + inter-sample weight exclusion (WHI-825)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from monitor.collector.gaps import (
    SOURCE_COLLECTOR_DOWN,
    collector_down_gap,
    inter_sample_weight_ms,
    interval_overlaps_gaps,
)
from monitor.metrics.session import SessionKind
from monitor.metrics.stats import EdgeStats
from monitor.quotes import BybitBookTick
from monitor.storage import JournalReader, SqliteStore


def test_collector_down_gap_records_interval() -> None:
    gap = collector_down_gap(
        last_write_ms=1_000_000,
        first_write_ms=1_000_000 + 90 * 60_000,  # 90 min
        min_gap_ms=30_000,
        market_id="binance-pancake",
    )
    assert gap is not None
    assert gap.source == SOURCE_COLLECTOR_DOWN
    assert gap.gap_start_ms == 1_000_000
    assert gap.gap_end_ms == 1_000_000 + 90 * 60_000
    assert "binance-pancake" in gap.detail
    assert "duration_ms=" in gap.detail


def test_collector_down_gap_skips_short_bounce() -> None:
    assert (
        collector_down_gap(
            last_write_ms=1_000,
            first_write_ms=1_000 + 10_000,  # 10s
            min_gap_ms=30_000,
        )
        is None
    )


def test_collector_down_gap_rejects_non_positive() -> None:
    assert (
        collector_down_gap(last_write_ms=100, first_write_ms=100, min_gap_ms=0)
        is None
    )
    assert (
        collector_down_gap(last_write_ms=200, first_write_ms=100, min_gap_ms=0)
        is None
    )


def test_interval_overlaps_gaps() -> None:
    gaps = [(1000, 5000)]
    assert interval_overlaps_gaps(900, 1100, gaps) is True
    assert interval_overlaps_gaps(4000, 6000, gaps) is True
    assert interval_overlaps_gaps(5000, 6000, gaps) is False  # touch end only
    assert interval_overlaps_gaps(100, 200, gaps) is False


def test_inter_sample_weight_zeros_on_max_gap() -> None:
    assert (
        inter_sample_weight_ms(0, 400_000, max_gap_ms=300_000) == 0
    )
    assert inter_sample_weight_ms(0, 10_000, max_gap_ms=300_000) == 10_000


def test_inter_sample_weight_zeros_on_collector_down_overlap() -> None:
    # 1.4h downtime recorded; samples straddle it but raw span < max_gap
    # (would otherwise inflate if max_gap were large).
    down = (10_000, 50_000)
    w = inter_sample_weight_ms(
        9_000,
        51_000,
        max_gap_ms=3_600_000,  # 1h cap would still allow 42s… use large cap
        exclude_intervals=[down],
    )
    assert w == 0
    # Adjacent sample pair fully after the gap keeps weight
    assert (
        inter_sample_weight_ms(
            51_000,
            52_000,
            max_gap_ms=300_000,
            exclude_intervals=[down],
        )
        == 1_000
    )


def test_store_records_collector_down_and_reader_sees_it(tmp_path: Path) -> None:
    """Restart-shaped: last write → first write becomes a journal gap row."""
    db = tmp_path / "g.db"
    store = SqliteStore(db)
    last = 1_000_000
    store.insert_bybit_book(
        [
            BybitBookTick(
                pair_id="AAPLx",
                symbol="AAPLUSDT",
                exchange_ts_ms=last,
                recv_ts_ms=last,
                bid=Decimal("100"),
                ask=Decimal("100.1"),
                bid_de_multiplied=Decimal("100"),
                ask_de_multiplied=Decimal("100.1"),
                multiplier=Decimal(1),
            )
        ]
    )
    freshest = store.freshest_recv_ts_ms()
    assert freshest == last
    first = last + 90 * 60_000
    gap = collector_down_gap(
        last_write_ms=freshest,
        first_write_ms=first,
        min_gap_ms=30_000,
        market_id="binance-pancake",
    )
    assert gap is not None
    store.insert_gap(gap)
    store.close()

    with JournalReader(db) as reader:
        gaps = reader.recent_gaps(since_ms=0, limit=10)
        assert any(g.source == SOURCE_COLLECTOR_DOWN for g in gaps)
        assert gaps[0].gap_start_ms == last
        assert gaps[0].gap_end_ms == first


def test_edge_stats_excludes_collector_down_interval() -> None:
    """Cumulative distribution must not time-weight across a known downtime."""
    down = (1_000, 1_000 + 90 * 60_000)
    stats = EdgeStats(
        max_gap_ms=3_600_000,  # 1h — would partially weight without exclude
        breach_size_usd=Decimal(1000),
        exclude_intervals=(down,),
    )
    # Sample just before outage, still "breaching"
    stats.observe(
        net_edge_bps=Decimal("50"),
        ts_ms=900,
        session=SessionKind.OPEN,
    )
    # First sample after restart — far past max would also zero, but we use
    # exclude_intervals so even mid-range spans zero.
    stats.observe(
        net_edge_bps=Decimal("50"),
        ts_ms=1_000 + 90 * 60_000 + 1_000,
        session=SessionKind.OPEN,
    )
    b = stats.breach_stats(SessionKind.OPEN)
    assert b.total_duration_ms == 0
    dist = stats.distribution(SessionKind.OPEN)
    assert dist.count == 2
    # Weights zeroed across gap → unit-weight fallback on both samples only
    # (no inflated time weight from the 90min hole).
    assert dist.p50 == Decimal("50")
