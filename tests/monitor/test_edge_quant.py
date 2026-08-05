"""Unit tests for M8 edge-window / single-flight analysis (WHI-866)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from monitor.analysis.edge_quant import (
    EdgeSample,
    OpportunityWindow,
    capturable_profit_single_flight,
    detect_windows,
    fit_min_edge_bps,
    portfolio_capturable_profit,
    threshold_sweep,
)


def _s(
    ts: int,
    *,
    edge: str | int | Decimal,
    pnl: str | int | Decimal,
    pair: str = "AAPLx",
    direction: str = "buy_fluxion_sell_bybit",
    session: str = "open",
    venue: str = "amm",
    size: str | int = 1000,
) -> EdgeSample:
    return EdgeSample(
        ts_ms=ts,
        pair_id=pair,
        direction=direction,
        session=session,  # type: ignore[arg-type]
        venue=venue,  # type: ignore[arg-type]
        size_usd=Decimal(size),
        edge_bps=Decimal(edge),
        pnl_usd=Decimal(pnl),
    )


class TestDetectWindows:
    def test_empty(self) -> None:
        assert detect_windows([], min_edge_bps=Decimal(0), max_gap_ms=60_000) == []

    def test_single_spike(self) -> None:
        samples = [
            _s(0, edge=-5, pnl=-1),
            _s(1000, edge=20, pnl=2),
            _s(2000, edge=25, pnl=2.5),  # peak later — trade uses fire-on-open
            _s(3000, edge=-1, pnl=-0.1),
        ]
        wins = detect_windows(samples, min_edge_bps=Decimal(10), max_gap_ms=60_000)
        assert len(wins) == 1
        w = wins[0]
        assert w.start_ms == 1000
        assert w.end_ms == 2000
        assert w.n_samples == 2
        assert w.trade_pnl_usd == Decimal("2")  # first sample, not peak 2.5
        assert w.peak_edge_bps == Decimal("25")

    def test_gap_breaks_window(self) -> None:
        samples = [
            _s(0, edge=20, pnl=1),
            _s(100_000, edge=20, pnl=1),  # 100s gap > 60s
        ]
        wins = detect_windows(samples, min_edge_bps=Decimal(0), max_gap_ms=60_000)
        assert len(wins) == 2

    def test_requires_positive_pnl(self) -> None:
        # Edge above threshold but non-positive pnl must not open a window.
        samples = [_s(0, edge=50, pnl=0), _s(1000, edge=50, pnl=-1)]
        wins = detect_windows(samples, min_edge_bps=Decimal(0), max_gap_ms=60_000)
        assert wins == []

    def test_rejects_unsorted(self) -> None:
        samples = [_s(2000, edge=10, pnl=1), _s(1000, edge=10, pnl=1)]
        with pytest.raises(ValueError, match="sorted"):
            detect_windows(samples, min_edge_bps=Decimal(0), max_gap_ms=60_000)

    def test_rejects_mixed_series(self) -> None:
        samples = [
            _s(0, edge=10, pnl=1, pair="AAPLx"),
            _s(1000, edge=10, pnl=1, pair="TSLAx"),
        ]
        with pytest.raises(ValueError, match="homogeneous"):
            detect_windows(samples, min_edge_bps=Decimal(0), max_gap_ms=60_000)


class TestCapturableProfit:
    def _win(
        self,
        start: int,
        end: int,
        pnl: str | int | Decimal,
        *,
        size: int = 1000,
        pair: str = "AAPLx",
    ) -> OpportunityWindow:
        return OpportunityWindow(
            pair_id=pair,
            direction="buy_fluxion_sell_bybit",
            session="open",
            venue="amm",
            size_usd=Decimal(size),
            start_ms=start,
            end_ms=end,
            n_samples=2,
            trade_pnl_usd=Decimal(pnl),
            peak_edge_bps=Decimal(20),
        )

    def test_one_trade_per_short_window(self) -> None:
        wins = [self._win(0, 1000, 5), self._win(10_000, 11_000, 3)]
        p = capturable_profit_single_flight(
            wins, reentry_cooldown_ms=5_000, trade_duration_ms=1_000
        )
        assert p == Decimal(8)

    def test_single_flight_skips_overlap(self) -> None:
        # Second window starts while first trade is still in flight.
        wins = [self._win(0, 2000, 10), self._win(500, 2500, 9)]
        p = capturable_profit_single_flight(
            wins, reentry_cooldown_ms=10_000, trade_duration_ms=5_000
        )
        # First trade at t=0; free for next window at 5_000 — second ended.
        assert p == Decimal(10)

    def test_reentry_inside_long_window(self) -> None:
        # Window lasts 60s; trade 1s + cooldown 9s → entries every 10s.
        wins = [self._win(0, 60_000, 1)]
        p = capturable_profit_single_flight(
            wins, reentry_cooldown_ms=9_000, trade_duration_ms=1_000
        )
        # entries at 0,10k,20k,30k,40k,50k,60k = 7
        assert p == Decimal(7)

    def test_one_entry_when_cooldown_exceeds_window(self) -> None:
        wins = [self._win(0, 60_000, 5), self._win(100_000, 120_000, 3)]
        p = capturable_profit_single_flight(
            wins, reentry_cooldown_ms=86_400_000, trade_duration_ms=1_000
        )
        assert p == Decimal(8)

    def test_scales_when_size_above_cap(self) -> None:
        wins = [self._win(0, 1000, 10, size=2000)]
        p = capturable_profit_single_flight(
            wins,
            reentry_cooldown_ms=60_000,
            trade_duration_ms=1_000,
            max_trade_usd=Decimal(1000),
        )
        assert p == Decimal(5)


class TestPortfolio:
    def test_picks_best_of_concurrent(self) -> None:
        # Both open at t=0 but end before the flight releases — only one trade.
        wins = [
            OpportunityWindow(
                pair_id="A",
                direction="d",
                session="open",
                venue="amm",
                size_usd=Decimal(1000),
                start_ms=0,
                end_ms=500,
                n_samples=1,
                trade_pnl_usd=Decimal(3),
                peak_edge_bps=Decimal(10),
            ),
            OpportunityWindow(
                pair_id="B",
                direction="d",
                session="open",
                venue="amm",
                size_usd=Decimal(1000),
                start_ms=0,
                end_ms=500,
                n_samples=1,
                trade_pnl_usd=Decimal(8),
                peak_edge_bps=Decimal(20),
            ),
        ]
        p = portfolio_capturable_profit(
            wins,
            reentry_cooldown_ms=60_000,
            trade_duration_ms=1_000,
            max_trade_usd=Decimal(1000),
            inventory_usd=Decimal(5000),
        )
        assert p == Decimal(8)

    def test_sequential_nonoverlap(self) -> None:
        wins = [
            OpportunityWindow(
                pair_id="A",
                direction="d",
                session="open",
                venue="amm",
                size_usd=Decimal(1000),
                start_ms=0,
                end_ms=2_000,
                n_samples=1,
                trade_pnl_usd=Decimal(4),
                peak_edge_bps=Decimal(10),
            ),
            OpportunityWindow(
                pair_id="B",
                direction="d",
                session="open",
                venue="amm",
                size_usd=Decimal(1000),
                start_ms=20_000,
                end_ms=25_000,
                n_samples=1,
                trade_pnl_usd=Decimal(5),
                peak_edge_bps=Decimal(10),
            ),
        ]
        p = portfolio_capturable_profit(
            wins,
            reentry_cooldown_ms=5_000,
            trade_duration_ms=1_000,
            max_trade_usd=Decimal(1000),
            inventory_usd=Decimal(5000),
        )
        assert p == Decimal(9)


class TestThresholdFit:
    def test_knee_highest_retaining_70pct(self) -> None:
        samples = []
        # Dense positive edge at ~30 bps for a while, then mild 5 bps noise.
        for i in range(10):
            samples.append(_s(i * 1000, edge=30, pnl=3))
        for i in range(10, 30):
            samples.append(_s(i * 1000, edge=5, pnl=0.2))
        sweep = threshold_sweep(
            samples,
            thresholds_bps=[Decimal(0), Decimal(4), Decimal(10), Decimal(20), Decimal(40)],
            max_gap_ms=60_000,
            reentry_cooldown_ms=100_000,  # one trade per window
            trade_duration_ms=1_000,
            span_ms=86_400_000,
        )
        fit = fit_min_edge_bps(sweep, capture_fraction=Decimal("0.70"))
        assert fit.fitted
        # At T=0 and T=4 the mild noise window still counts; at T=10+ only the
        # 30-bps window remains. Knee should sit at the highest T that still
        # keeps ≥70% of P0 — here the single fat window dominates.
        assert fit.min_edge_bps >= Decimal(10)
        assert fit.profit_at_fit > 0

    def test_unfitted_when_no_profit(self) -> None:
        samples = [_s(0, edge=-5, pnl=-1), _s(1000, edge=-2, pnl=-0.5)]
        sweep = threshold_sweep(
            samples,
            thresholds_bps=[Decimal(0), Decimal(10)],
            max_gap_ms=60_000,
            reentry_cooldown_ms=5_000,
            span_ms=86_400_000,
        )
        fit = fit_min_edge_bps(sweep)
        assert fit.fitted is False
        assert fit.profit_at_zero == 0
