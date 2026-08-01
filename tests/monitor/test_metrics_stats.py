"""Seam: EdgeStats percentiles + cost-floor breach duration/count."""

from __future__ import annotations

from decimal import Decimal

from monitor.metrics import EdgeResult, EdgeStats, SessionKind, load_metrics_config
from monitor.metrics.edge import CostBreakdown
from monitor.metrics.stats import Distribution, SessionBuckets


def test_distribution_percentiles() -> None:
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
    stats = EdgeStats(max_gap_ms=300_000)
    stats.observe(net_edge_bps=Decimal("-1"), ts_ms=0, session=SessionKind.CLOSED)
    stats.observe(net_edge_bps=Decimal("5"), ts_ms=1000, session=SessionKind.OPEN)
    stats.observe(net_edge_bps=Decimal("3"), ts_ms=4000, session=SessionKind.OPEN)
    stats.observe(net_edge_bps=Decimal("-2"), ts_ms=5000, session=SessionKind.OPEN)
    stats.observe(net_edge_bps=Decimal("1"), ts_ms=7000, session=SessionKind.OPEN)

    b_all = stats.breach_stats(None)
    assert b_all.episode_count == 2
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


def test_overnight_gap_does_not_inflate_open_duration() -> None:
    """15:59 → next-day 09:30 must not add ~17.5h to open breach duration."""
    stats = EdgeStats(max_gap_ms=300_000)  # 5 min
    # Last open sample of the day, still breaching
    stats.observe(net_edge_bps=Decimal("10"), ts_ms=0, session=SessionKind.OPEN)
    # Next open sample ~17.5 hours later
    day_ms = 17 * 3600 * 1000 + 30 * 60 * 1000
    stats.observe(net_edge_bps=Decimal("10"), ts_ms=day_ms, session=SessionKind.OPEN)
    b = stats.breach_stats(SessionKind.OPEN)
    assert b.episode_count == 1  # still same episode conceptually, or 2 if reset
    # Critical: duration must NOT include the overnight gap
    assert b.total_duration_ms == 0


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
    assert stats.distribution(SessionKind.OPEN).count == 2


def test_observe_edge_respects_breach_size() -> None:
    cfg = load_metrics_config()
    stats = EdgeStats.from_config(cfg)
    costs = CostBreakdown(
        bybit_taker_bps=Decimal(10),
        fluxion_fee_bps=Decimal(0),
        bybit_slip_bps=Decimal(0),
        fluxion_slip_bps=Decimal(0),
        gas_bps=Decimal(0),
        basis_bps=Decimal(0),
    )
    big = EdgeResult(
        pair_id="TSLAx",
        venue="rfq",
        direction="buy_fluxion_sell_bybit",
        size_usd=Decimal(20000),  # not breach_size
        bybit_mid=Decimal(100),
        fluxion_mid=Decimal(99),
        gross_spread_bps=Decimal(100),
        costs=costs,
        net_edge_bps=Decimal(50),
        fillable=True,
    )
    small = EdgeResult(
        pair_id="TSLAx",
        venue="rfq",
        direction="buy_fluxion_sell_bybit",
        size_usd=cfg.breach_size_usd,
        bybit_mid=Decimal(100),
        fluxion_mid=Decimal(99),
        gross_spread_bps=Decimal(100),
        costs=costs,
        net_edge_bps=Decimal(50),
        fillable=True,
    )
    stats.observe_edge(big, ts_ms=0, session=SessionKind.OPEN, config=cfg)
    assert stats.distribution(SessionKind.OPEN).count == 0
    stats.observe_edge(small, ts_ms=1000, session=SessionKind.OPEN, config=cfg)
    assert stats.distribution(SessionKind.OPEN).count == 1
    assert stats.breach_stats(SessionKind.OPEN).episode_count == 1


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
