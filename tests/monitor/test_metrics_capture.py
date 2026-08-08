"""Seam: monitor.metrics.capture pure stats (WHI-963)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from monitor.analysis.edge_quant import EdgeSample, capturable_profit_single_flight
from monitor.metrics.capture import (
    CaptureSparkPoint,
    compute_capture_from_samples,
    series_stats,
    sparkline_from_windows,
)
from monitor.metrics.config import CaptureConfig


def _s(
    ts: int,
    *,
    edge: str | int | Decimal = 20,
    pnl: str | int | Decimal = 2,
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


def _cfg(**overrides: object) -> CaptureConfig:
    base = {
        "enabled": True,
        "lookback_ms": 86_400_000,
        "sample_ms": 30_000,
        "align_ms": 15_000,
        "max_gap_ms": 60_000,
        "trade_duration_ms": 5_000,
        "reentry_cooldown_ms": 86_400_000,  # one entry per window
        "size_usd": 1000,
        "min_edge_bps": 0,
        "include_rfq": False,
        "sparkline_bucket_ms": 3_600_000,
        "use_depth": True,
    }
    base.update(overrides)
    return CaptureConfig.model_validate(base)


class TestSeriesStats:
    def test_empty_returns_none(self) -> None:
        assert (
            series_stats(
                [],
                span_ms=86_400_000,
                max_gap_ms=60_000,
                reentry_cooldown_ms=5_000,
                trade_duration_ms=1_000,
            )
            is None
        )

    def test_matches_edge_quant_single_flight(self) -> None:
        # Two short windows an hour apart — one entry each under long cooldown.
        samples = [
            _s(0, pnl=5),
            _s(1_000, pnl=5),
            _s(3_600_000, pnl=3),
            _s(3_601_000, pnl=3),
        ]
        span = 86_400_000
        st = series_stats(
            samples,
            span_ms=span,
            max_gap_ms=60_000,
            reentry_cooldown_ms=86_400_000,
            trade_duration_ms=5_000,
            max_trade_usd=Decimal(1000),
        )
        assert st is not None
        assert st.n_windows == 2
        assert st.capturable_usd == Decimal(8)
        # per day over 1-day span
        assert st.capturable_usd_per_day == Decimal(8)
        assert st.windows_per_day == pytest.approx(2.0)

        # Cross-check pure helper used by xstocks_edge_quant.
        from monitor.analysis.edge_quant import detect_windows

        wins = detect_windows(samples, min_edge_bps=Decimal(0), max_gap_ms=60_000)
        p = capturable_profit_single_flight(
            wins, reentry_cooldown_ms=86_400_000, trade_duration_ms=5_000
        )
        assert p == st.capturable_usd


class TestComputeCapture:
    def test_no_samples(self) -> None:
        snap = compute_capture_from_samples(
            [],
            pair_id="AAPLx",
            since_ms=0,
            until_ms=86_400_000,
            capture=_cfg(),
        )
        assert snap.status == "no_samples"
        assert snap.capturable_usd_per_day is None
        assert snap.overview_wire()["status"] == "no_samples"

    def test_headline_sums_sessions_for_best_direction(self) -> None:
        samples = [
            # open session: 1 window pnl=5
            _s(1_000, pnl=5, session="open"),
            _s(2_000, pnl=5, session="open"),
            # closed: 1 window pnl=3, same direction
            _s(50_000_000, pnl=3, session="closed"),
            _s(50_001_000, pnl=3, session="closed"),
            # other direction weaker
            _s(
                1_000,
                pnl=1,
                direction="buy_bybit_sell_fluxion",
                session="open",
            ),
        ]
        snap = compute_capture_from_samples(
            samples,
            pair_id="AAPLx",
            since_ms=0,
            until_ms=86_400_000,
            capture=_cfg(),
        )
        assert snap.status == "ok"
        assert snap.direction == "buy_fluxion_sell_bybit"
        assert snap.n_windows == 2
        assert snap.capturable_usd_per_day == Decimal(8)
        assert snap.windows_per_day == pytest.approx(2.0)
        # Detail series includes both sessions.
        sessions = {s.session for s in snap.series if s.direction == snap.direction}
        assert sessions == {"open", "closed"}

    def test_overview_wire_compact(self) -> None:
        samples = [_s(0, pnl=2), _s(1000, pnl=2)]
        snap = compute_capture_from_samples(
            samples,
            pair_id="AAPLx",
            since_ms=0,
            until_ms=86_400_000,
            capture=_cfg(),
        )
        wire = snap.overview_wire()
        assert "series" not in wire
        assert "sparkline" not in wire
        assert wire["capturable_usd_per_day"] == "2"
        full = snap.to_dict()
        assert "series" in full
        assert "sparkline" in full


class TestSparkline:
    def test_buckets_windows_by_start(self) -> None:
        from monitor.analysis.edge_quant import OpportunityWindow

        def w(start: int, end: int, pnl: int) -> OpportunityWindow:
            return OpportunityWindow(
                pair_id="AAPLx",
                direction="buy_fluxion_sell_bybit",
                session="open",
                venue="amm",
                size_usd=Decimal(1000),
                start_ms=start,
                end_ms=end,
                n_samples=1,
                trade_pnl_usd=Decimal(pnl),
                peak_edge_bps=Decimal(20),
            )

        pts = sparkline_from_windows(
            [w(100, 200, 1), w(3_600_100, 3_600_200, 2)],
            since_ms=0,
            until_ms=7_200_000,
            bucket_ms=3_600_000,
        )
        assert len(pts) == 3  # 0, 1h, 2h
        assert pts[0] == CaptureSparkPoint(
            bucket_start_ms=0, n_windows=1, capturable_usd=Decimal(1)
        )
        assert pts[1].n_windows == 1
        assert pts[1].capturable_usd == Decimal(2)
        assert pts[2].n_windows == 0


def test_capture_config_defaults_load() -> None:
    from monitor.metrics.config import load_metrics_config

    cfg = load_metrics_config()
    assert cfg.capture.enabled is True
    assert cfg.capture.trade_duration_ms == 390_000
    assert cfg.capture.reentry_cooldown_ms == 420_000
    assert cfg.capture.size_usd == Decimal(1000)
