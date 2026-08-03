"""Seam: collector_down gap accounting + inter-sample weight exclusion (WHI-825)."""

from __future__ import annotations

from decimal import Decimal

from monitor.collector.gaps import (
    SOURCE_COLLECTOR_DOWN,
    collector_down_gap,
    inter_sample_weight_ms,
    interval_overlaps_gaps,
)
from monitor.metrics.session import SessionKind
from monitor.metrics.stats import EdgeStats


def test_collector_down_gap_records_interval() -> None:
    gap = collector_down_gap(
        last_write_ms=1_000_000,
        first_write_ms=1_000_000 + 90 * 60_000,  # 90 min
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
    assert collector_down_gap(last_write_ms=100, first_write_ms=100) is None
    assert collector_down_gap(last_write_ms=200, first_write_ms=100) is None


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
