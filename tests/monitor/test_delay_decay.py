"""Unit tests for M8 delay-decay / sequential-cycle helpers (WHI-915)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from monitor.analysis.delay_decay import (
    SequentialOutcome,
    clip_size_sweep,
    distribution_stats,
    fit_drift_premium_k,
    log_return_bps,
    optimal_clip_size,
    portfolio_sequential_profit,
    sample_std,
    sequential_capturable_profit,
    transit_sigma_bps,
    withdraw_fee_bps,
)


def _o(
    ts: int,
    *,
    edge: str | int | Decimal,
    sim_pnl: str | int | Decimal = 1,
    realised: str | int | Decimal,
    realised_bps: str | int | Decimal | None = None,
    pair: str = "HOODx",
    session: str = "open",
    size: int = 500,
    lag_ms: int = 600_000,
    fillable: bool = True,
) -> SequentialOutcome:
    r = Decimal(realised)
    rb = Decimal(realised_bps) if realised_bps is not None else (
        r / Decimal(size) * Decimal(10_000) if size else Decimal(0)
    )
    return SequentialOutcome(
        pair_id=pair,
        session=session,  # type: ignore[arg-type]
        size_usd=Decimal(size),
        lag_ms=lag_ms,
        entry_ts_ms=ts,
        simultaneous_edge_bps=Decimal(edge),
        simultaneous_pnl_usd=Decimal(sim_pnl),
        realised_pnl_usd=r,
        realised_bps=rb,
        fillable=fillable,
    )


class TestDistributionStats:
    def test_empty(self) -> None:
        s = distribution_stats([])
        assert s.n == 0
        assert s.mean is None
        assert s.win_rate is None

    def test_tails_and_win_rate(self) -> None:
        vals = [Decimal(x) for x in (-10, -2, 0, 1, 5, 10, 20)]
        s = distribution_stats(vals)
        assert s.n == 7
        assert s.worst == Decimal(-10)
        assert s.best == Decimal(20)
        assert s.n_wins == 4  # 1,5,10,20
        assert s.win_rate == Decimal(4) / Decimal(7)
        assert s.median == Decimal(1)
        assert s.p5 == Decimal(-10)  # nearest-rank at low n
        assert s.mean is not None
        assert s.mean == sum(vals, start=Decimal(0)) / 7


class TestLogReturnAndSigma:
    def test_log_return_zero_for_flat(self) -> None:
        r = log_return_bps(Decimal("100"), Decimal("100"))
        assert r is not None
        assert abs(r) < Decimal("0.001")

    def test_log_return_positive_up_move(self) -> None:
        # ~100 bps up: ln(1.01)*1e4 ≈ 99.5
        r = log_return_bps(Decimal("100"), Decimal("101"))
        assert r is not None
        assert Decimal("90") < r < Decimal("110")

    def test_rejects_nonpositive(self) -> None:
        assert log_return_bps(Decimal(0), Decimal(1)) is None
        assert log_return_bps(Decimal(1), Decimal(0)) is None

    def test_sample_std_needs_two(self) -> None:
        assert sample_std([Decimal(1)]) is None
        assert sample_std([]) is None
        s = sample_std([Decimal(1), Decimal(3)])
        assert s is not None
        assert abs(s - Decimal("1.41421356237")) < Decimal("0.01")

    def test_transit_sigma_flat_series(self) -> None:
        mids = [(i * 60_000, Decimal("100")) for i in range(20)]
        sig, n = transit_sigma_bps(mids, lag_ms=300_000, max_align_ms=30_000)
        assert n > 0
        assert sig is not None
        assert sig == Decimal(0) or abs(sig) < Decimal("0.01")

    def test_transit_sigma_known_move(self) -> None:
        # Every 5 min mid jumps +1% once then flat — lag=5m catches the jump.
        mids = [
            (0, Decimal("100")),
            (300_000, Decimal("101")),
            (600_000, Decimal("101")),
            (900_000, Decimal("101")),
        ]
        sig, n = transit_sigma_bps(mids, lag_ms=300_000, max_align_ms=1_000)
        assert n >= 2
        assert sig is not None
        assert sig > 0


class TestWithdrawFeeAndClip:
    def test_withdraw_fee_bps(self) -> None:
        assert withdraw_fee_bps(Decimal(500), Decimal(1)) == Decimal(20)
        assert withdraw_fee_bps(Decimal(1000), Decimal(1)) == Decimal(10)

    def test_clip_sweep_and_optimum(self) -> None:
        by_size = {
            Decimal(100): [
                _o(0, edge=50, realised="0.5", size=100),
                _o(1, edge=50, realised="0.4", size=100),
            ],
            Decimal(500): [
                _o(0, edge=50, realised="2.0", size=500),
                _o(1, edge=50, realised="1.5", size=500),
            ],
            Decimal(1000): [
                _o(0, edge=50, realised="1.0", size=1000),
            ],
        }
        rows = clip_size_sweep(by_size, withdraw_fee_usd=Decimal(1))
        assert len(rows) == 3
        opt = optimal_clip_size(rows)
        assert opt is not None
        assert opt.size_usd == Decimal(500)  # mean USD 1.75 > others

    def test_optimal_ignores_all_negative(self) -> None:
        by_size = {
            Decimal(100): [_o(0, edge=50, realised="-1", size=100)],
            Decimal(500): [_o(0, edge=50, realised="-2", size=500)],
        }
        rows = clip_size_sweep(by_size, withdraw_fee_usd=Decimal(1))
        assert optimal_clip_size(rows) is None


class TestDriftPremiumK:
    def test_higher_edge_admits_better_win_rate(self) -> None:
        # Low edge → often lose; high edge → always win.
        outs = [
            _o(0, edge=10, realised=-1),
            _o(1, edge=12, realised=-1),
            _o(2, edge=15, realised=1),
            _o(3, edge=40, realised=2),
            _o(4, edge=50, realised=3),
            _o(5, edge=55, realised=2),
            _o(6, edge=60, realised=1),
            _o(7, edge=70, realised=2),
        ]
        # sigma=10 → k=1 requires edge>=10 (all); k=4 requires edge>=40 (all wins)
        fits = fit_drift_premium_k(
            outs,
            sigma_bps=Decimal(10),
            targets=[Decimal("0.90")],
            k_max=Decimal("5"),
            k_step=Decimal("0.5"),
            min_admitted=3,
        )
        assert len(fits) == 1
        f = fits[0]
        assert f.reachable
        assert f.k is not None
        # k=1.5 → edge ≥ 15 admits the six winners (excludes two losers at 10/12)
        assert f.k == Decimal("1.5")
        assert f.realised_win_rate == Decimal(1)

    def test_unreachable_when_wins_never_enough(self) -> None:
        outs = [_o(i, edge=100, realised=-1) for i in range(10)]
        fits = fit_drift_premium_k(
            outs,
            sigma_bps=Decimal(10),
            targets=[Decimal("0.90")],
            min_admitted=3,
        )
        assert fits[0].reachable is False


class TestCapturable:
    def test_single_flight_skips_overlapping(self) -> None:
        outs = [
            _o(0, edge=50, realised=2),
            _o(1_000, edge=50, realised=3),  # inside flight+cooldown
            _o(100_000, edge=50, realised=4),
        ]
        p = sequential_capturable_profit(
            outs,
            reentry_cooldown_ms=50_000,
            trade_duration_ms=5_000,
            min_simultaneous_edge_bps=Decimal(0),
        )
        # step = 55s → second skipped, third taken → 2+4
        assert p == Decimal(6)

    def test_books_negative_realised(self) -> None:
        # Look-ahead fix: losers still consume the slot and hit PnL.
        outs = [
            _o(0, edge=50, realised=-3),
            _o(100_000, edge=50, realised=2),
        ]
        p = sequential_capturable_profit(
            outs,
            reentry_cooldown_ms=0,
            trade_duration_ms=1,
            min_simultaneous_edge_bps=Decimal(0),
        )
        assert p == Decimal(-1)

    def test_portfolio_ranks_by_simultaneous_edge(self) -> None:
        # At t=0 two symbols compete: higher simultaneous edge wins admission
        # even if its realised PnL is worse (no look-ahead).
        outs = [
            _o(0, edge=20, realised=10, pair="A"),
            _o(0, edge=80, realised=-1, pair="B"),
            _o(600_000, edge=50, realised=2, pair="A"),
        ]
        p = portfolio_sequential_profit(
            outs,
            trade_duration_ms=600_000,  # full transit lock
            min_simultaneous_edge_bps=Decimal(0),
        )
        # t=0 takes B (edge 80) → -1; free at 600s; takes A → 2; total +1
        assert p == Decimal(1)

    def test_admission_filters_edge(self) -> None:
        outs = [
            _o(0, edge=5, realised=10),
            _o(100_000, edge=50, realised=1),
        ]
        p = sequential_capturable_profit(
            outs,
            reentry_cooldown_ms=0,
            trade_duration_ms=1,
            min_simultaneous_edge_bps=Decimal(20),
        )
        assert p == Decimal(1)


class TestErrors:
    def test_transit_sigma_bad_lag(self) -> None:
        with pytest.raises(ValueError):
            transit_sigma_bps([(0, Decimal(1))], lag_ms=0, max_align_ms=1)
