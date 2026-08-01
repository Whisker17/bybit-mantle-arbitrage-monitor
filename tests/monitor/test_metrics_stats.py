"""Seam: EdgeStats percentiles + cost-floor breach duration/count."""

from __future__ import annotations

from decimal import Decimal

from monitor.metrics import EdgeStats, SessionKind
from monitor.metrics.stats import Distribution, SessionBuckets


def test_distribution_percentiles() -> None:
    # 100 values 1..100
    vals = [Decimal(i) for i in range(1, 101)]
    d = Distribution.from_values(vals)
    assert d.count == 100
    assert d.p50 == Decimal(50)
    assert d.p95 == Decimal(95)
    assert d.p99 == Decimal(99)
    assert d.max == Decimal(100)


def test_distribution_empty() -> None:
    d = Distribution.from_values([])
    assert d.count == 0
    assert d.p50 is None


def test_breach_episodes_and_duration() -> None:
    stats = EdgeStats()
    # t=0 closed, net=-1 (no breach)
    stats.observe(
        net_edge_bps=Decimal("-1"),
        ts_ms=0,
        session=SessionKind.CLOSED,
    )
    # t=1000 open, net=+5 → episode starts
    stats.observe(
        net_edge_bps=Decimal("5"),
        ts_ms=1000,
        session=SessionKind.OPEN,
    )
    # t=4000 open, still + → duration +3000 on open/all
    stats.observe(
        net_edge_bps=Decimal("3"),
        ts_ms=4000,
        session=SessionKind.OPEN,
    )
    # t=5000 open, net=-2 → breach ends; duration +1000
    stats.observe(
        net_edge_bps=Decimal("-2"),
        ts_ms=5000,
        session=SessionKind.OPEN,
    )
    # t=7000 open, net=+1 → second episode
    stats.observe(
        net_edge_bps=Decimal("1"),
        ts_ms=7000,
        session=SessionKind.OPEN,
    )

    b_all = stats.breach_stats(None)
    assert b_all.episode_count == 2
    # durations while breaching: 1000→4000 (3000) + 4000→5000 (1000) = 4000
    assert b_all.total_duration_ms == 4000
    assert b_all.currently_breaching is True

    b_open = stats.breach_stats(SessionKind.OPEN)
    assert b_open.episode_count == 2
    assert b_open.total_duration_ms == 4000

    b_closed = stats.breach_stats(SessionKind.CLOSED)
    assert b_closed.episode_count == 0
    assert b_closed.total_duration_ms == 0

    dist_open = stats.distribution(SessionKind.OPEN)
    assert dist_open.count == 4
    assert dist_open.max == Decimal("5")


def test_non_fillable_does_not_count_breach() -> None:
    stats = EdgeStats()
    stats.observe(
        net_edge_bps=Decimal("50"),
        ts_ms=0,
        session=SessionKind.OPEN,
        fillable=False,
    )
    stats.observe(
        net_edge_bps=Decimal("50"),
        ts_ms=1000,
        session=SessionKind.OPEN,
        fillable=False,
    )
    b = stats.breach_stats(SessionKind.OPEN)
    assert b.episode_count == 0
    assert b.total_duration_ms == 0
    assert b.currently_breaching is False
    # Still in distribution
    assert stats.distribution(SessionKind.OPEN).count == 2


def test_session_buckets_snapshot() -> None:
    stats = EdgeStats()
    stats.observe(net_edge_bps=Decimal("1"), ts_ms=0, session=SessionKind.OPEN)
    stats.observe(net_edge_bps=Decimal("-1"), ts_ms=100, session=SessionKind.CLOSED)
    buckets = SessionBuckets.from_stats(stats)
    assert buckets.all.count == 2
    assert buckets.open.count == 1
    assert buckets.closed.count == 1
    assert buckets.breach_open.episode_count == 1
    assert buckets.breach_closed.episode_count == 0
