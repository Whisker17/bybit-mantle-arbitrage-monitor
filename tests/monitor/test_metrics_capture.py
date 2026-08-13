"""Seam: monitor.metrics.capture pure stats (WHI-963)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from monitor.analysis.edge_quant import EdgeSample, capturable_profit_single_flight
from monitor.metrics.amm_pool import amm_pool_from_pair_tick
from monitor.metrics.capture import (
    CaptureSparkPoint,
    build_amm_samples,
    compute_capture_from_samples,
    series_stats,
    sparkline_from_windows,
)
from monitor.metrics.config import CaptureConfig, load_metrics_config
from monitor.metrics.pnl_v2 import compute_pnl_usd
from monitor.metrics.withdrawal import withdrawal_params_from_pair
from monitor.quotes import BybitBookTick, FluxionPoolStateTick
from monitor.symbols import load_pairs_config

_TS_MS = int(datetime(2026, 1, 15, 16, 0, tzinfo=UTC).timestamp() * 1000)


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
        result = series_stats(
            samples,
            span_ms=span,
            max_gap_ms=60_000,
            reentry_cooldown_ms=86_400_000,
            trade_duration_ms=5_000,
            max_trade_usd=Decimal(1000),
        )
        assert result is not None
        st, wins = result
        assert st.n_windows == 2
        assert st.capturable_usd == Decimal(8)
        # per day over 1-day span
        assert st.capturable_usd_per_day == Decimal(8)
        assert st.windows_per_day == pytest.approx(2.0)
        assert len(wins) == 2

        # Cross-check pure helper used by xstocks_edge_quant.
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
        # Rates use actual coverage span (~50_001_000 ms), not full lookback.
        assert snap.session is None  # open+closed summed
        assert snap.capturable_usd_per_day is not None
        assert snap.capturable_usd_per_day > Decimal(8)  # denser than 1d span
        # Detail series includes both sessions.
        sessions = {s.session for s in snap.series if s.direction == snap.direction}
        assert sessions == {"open", "closed"}

    def test_rates_use_sample_coverage_not_lookback(self) -> None:
        # Dense 1h of in-edge samples under a 24h lookback → one window,
        # rates annualize by coverage (1h), not by the empty rest of the day.
        samples = [_s(t, pnl=10) for t in range(0, 3_600_000 + 1, 30_000)]
        snap = compute_capture_from_samples(
            samples,
            pair_id="AAPLx",
            since_ms=0,
            until_ms=86_400_000,
            capture=_cfg(lookback_ms=86_400_000, max_gap_ms=60_000),
        )
        assert snap.status == "ok"
        assert snap.span_ms == 3_600_000
        assert snap.n_windows == 1
        assert snap.windows_per_day == pytest.approx(24.0)
        assert snap.capturable_usd_per_day == Decimal(240)

    def test_overview_wire_compact(self) -> None:
        # Full-day coverage so $/day equals the single trade PnL.
        samples = [_s(0, pnl=2), _s(86_400_000, pnl=-1)]  # only first is a window
        # Two positive samples a day apart → two windows / 1d span → $4/day.
        samples = [_s(0, pnl=2), _s(1_000, pnl=2), _s(86_400_000, pnl=2)]
        snap = compute_capture_from_samples(
            samples,
            pair_id="AAPLx",
            since_ms=0,
            until_ms=86_400_000,
            capture=_cfg(max_gap_ms=60_000),
        )
        wire = snap.overview_wire()
        assert "series" not in wire
        assert "sparkline" not in wire
        assert wire["status"] == "ok"
        assert wire["capturable_usd_per_day"] is not None
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


class TestWithdrawalOnAmmSamples:
    """WHI-1090: Cap $/d must charge measured dir2 fees and skip unknown."""

    def _book(self, pair_id: str, symbol: str, *, mid: Decimal, ts: int = _TS_MS) -> BybitBookTick:
        return BybitBookTick(
            pair_id=pair_id,
            symbol=symbol,
            exchange_ts_ms=ts,
            recv_ts_ms=ts,
            bid=mid,
            ask=mid,
            bid_de_multiplied=mid,
            ask_de_multiplied=mid,
            multiplier=Decimal(1),
        )

    def _pool(
        self, pair_id: str, pool: str, *, mid: Decimal, ts: int = _TS_MS
    ) -> FluxionPoolStateTick:
        ratio = Decimal(10) ** 12 / mid
        sqrt_price_x96 = int(ratio.sqrt() * Decimal(2**96))
        return FluxionPoolStateTick(
            pair_id=pair_id,
            pool=pool,
            block_number=1,
            block_ts=ts // 1000,
            recv_ts_ms=ts,
            sqrt_price_x96=sqrt_price_x96,
            tick=0,
            liquidity=10**20,
            token0="0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9",
            token1="0x5aa7649fdbda47de64a07ac81d64b682af9c0724",
            mid_usdc_per_wrapper=mid,
            mid_usdc_per_native=mid,
            wrapper_assets_per_share=Decimal(1),
        )

    def _samples(self, pair):
        book = self._book(pair.id, pair.bybit.symbol, mid=Decimal(100))
        pool = self._pool(
            pair.id,
            pair.fluxion.amm.pool,  # type: ignore[union-attr]
            mid=Decimal("101"),
        )
        return build_amm_samples(
            pair=pair,
            books=[book],
            pools=[pool],
            depths=[],
            metrics_cfg=load_metrics_config(),
            quote_decimals=6,
            size_usd=Decimal(1000),
            gaps=[],
            align_ms=15_000,
            max_abs_spread_bps=Decimal(300),
        )

    def test_unknown_fee_pair_emits_no_dir2_samples(self) -> None:
        spcx = load_pairs_config().pair_by_id("SPCXx")
        assert spcx.asset_withdrawal_fee_tokens is None
        samples = self._samples(spcx)
        dirs = {s.direction for s in samples}
        assert "buy_bybit_sell_fluxion" not in dirs

    def test_measured_fee_pair_charges_dir2_withdrawal(self) -> None:
        hood = load_pairs_config().pair_by_id("HOODx")
        wd = withdrawal_params_from_pair(hood)
        assert wd.asset_fee_tokens == Decimal("0.01")
        samples = self._samples(hood)
        dir2 = [s for s in samples if s.direction == "buy_bybit_sell_fluxion"]
        assert dir2, "measured-fee dir2 must still participate in Cap $/d"
        book = self._book(hood.id, hood.bybit.symbol, mid=Decimal(100))
        pool = self._pool(
            hood.id, hood.fluxion.amm.pool, mid=Decimal("101")  # type: ignore[union-attr]
        )
        amm = amm_pool_from_pair_tick(hood, pool, quote_decimals=6)
        cfg = load_metrics_config()
        unpriced = compute_pnl_usd(
            pair_id=hood.id,
            bybit_bid=book.bid_de_multiplied,
            bybit_ask=book.ask_de_multiplied,
            size_usd=Decimal(1000),
            direction="buy_bybit_sell_fluxion",
            venue="amm",
            config=cfg,
            amm=amm,
        )
        priced = compute_pnl_usd(
            pair_id=hood.id,
            bybit_bid=book.bid_de_multiplied,
            bybit_ask=book.ask_de_multiplied,
            size_usd=Decimal(1000),
            direction="buy_bybit_sell_fluxion",
            venue="amm",
            config=cfg,
            amm=amm,
            asset_withdrawal_fee_tokens=wd.asset_fee_tokens,
            price_multiplier=wd.price_multiplier,
        )
        assert dir2[0].pnl_usd == priced.pnl_usd
        assert dir2[0].pnl_usd < unpriced.pnl_usd


def test_capture_config_defaults_load() -> None:
    cfg = load_metrics_config()
    assert cfg.capture.enabled is True
    assert cfg.capture.trade_duration_ms == 390_000
    assert cfg.capture.reentry_cooldown_ms == 420_000
    assert cfg.capture.size_usd == Decimal(1000)


class TestLiveBasisJoin:
    """WHI-909: live basis series must not silently fall back to constant."""

    def test_config_with_live_basis_requires_join(self) -> None:
        from monitor.metrics.capture import _config_with_live_basis
        from monitor.metrics.config import load_metrics_config

        cfg = load_metrics_config()
        # Series present but sample too far after last bar → None (skip sample).
        out = _config_with_live_basis(
            cfg,
            1_000_000,
            basis_ts_ms=(0,),
            basis_bps_series=(Decimal("7.5"),),
            basis_max_age_ms=60_000,
        )
        assert out is None

    def test_config_with_live_basis_applies_as_of(self) -> None:
        from monitor.metrics.capture import _config_with_live_basis
        from monitor.metrics.config import load_metrics_config

        cfg = load_metrics_config()
        out = _config_with_live_basis(
            cfg,
            50_000,
            basis_ts_ms=(0, 60_000),
            basis_bps_series=(Decimal("5"), Decimal("8")),
            basis_max_age_ms=120_000,
        )
        assert out is not None
        assert out.usdt_usdc_basis_bps == Decimal("5")

    def test_config_without_series_uses_constant(self) -> None:
        from monitor.metrics.capture import _config_with_live_basis
        from monitor.metrics.config import load_metrics_config

        cfg = load_metrics_config()
        out = _config_with_live_basis(
            cfg,
            1,
            basis_ts_ms=None,
            basis_bps_series=None,
            basis_max_age_ms=None,
        )
        assert out is cfg
