"""Delay-decay / sequential-cycle statistics (WHI-915 / M8).

Pure helpers for the zero-inventory transfer cycle: realised PnL when the
Fluxion buy is at ``t`` and the Bybit sell is at ``t + N``, transit-window
σ from Bybit mid log-returns, and ``drift_premium_k`` so the bot's admission
rule can require simultaneous edge ≥ k · σ_transit.

Callers supply already-computed outcomes (or mids). This module never
reimplements venue fees, AMM impact, or book VWAP — that stays in
``monitor.metrics.pnl_v2`` / the journal driver script.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

SessionKind = Literal["open", "closed"]

# Default admission targets the engine may consume (bot DESIGN §2.4 / §2.8).
DEFAULT_WIN_RATE_TARGETS: tuple[Decimal, ...] = (
    Decimal("0.90"),
    Decimal("0.95"),
    Decimal("0.99"),
)


@dataclass(frozen=True, slots=True)
class DistStats:
    """Distribution summary for a realised-PnL (or bps) series."""

    n: int
    mean: Decimal | None
    median: Decimal | None
    p5: Decimal | None
    p25: Decimal | None
    p75: Decimal | None
    p95: Decimal | None
    worst: Decimal | None  # min (most negative)
    best: Decimal | None
    win_rate: Decimal | None  # fraction with value > 0
    n_wins: int

    def to_dict(self) -> dict[str, object]:
        def _s(v: Decimal | None) -> str | None:
            return None if v is None else str(v)

        return {
            "n": self.n,
            "mean": _s(self.mean),
            "median": _s(self.median),
            "p5": _s(self.p5),
            "p25": _s(self.p25),
            "p75": _s(self.p75),
            "p95": _s(self.p95),
            "worst": _s(self.worst),
            "best": _s(self.best),
            "win_rate": _s(self.win_rate),
            "n_wins": self.n_wins,
        }


@dataclass(frozen=True, slots=True)
class SequentialOutcome:
    """One reconstructed sequential cycle (direction 1 only).

    ``simultaneous_edge_bps`` / ``simultaneous_pnl_usd`` are the same-timestamp
    paper edge (M0 baseline). ``realised_*`` use Bybit sell at entry + lag.
    """

    pair_id: str
    session: SessionKind
    size_usd: Decimal
    lag_ms: int
    entry_ts_ms: int
    simultaneous_edge_bps: Decimal
    simultaneous_pnl_usd: Decimal
    realised_pnl_usd: Decimal
    realised_bps: Decimal
    fillable: bool = True


@dataclass(frozen=True, slots=True)
class DriftPremiumFit:
    """Minimal k such that edge ≥ k·σ yields the target realised win rate."""

    target_win_rate: Decimal
    k: Decimal | None
    n_admitted: int
    realised_win_rate: Decimal | None
    sigma_bps: Decimal
    # True when no finite k on the searched grid reaches the target.
    reachable: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "target_win_rate": str(self.target_win_rate),
            "k": None if self.k is None else str(self.k),
            "n_admitted": self.n_admitted,
            "realised_win_rate": (
                None if self.realised_win_rate is None else str(self.realised_win_rate)
            ),
            "sigma_bps": str(self.sigma_bps),
            "reachable": self.reachable,
        }


@dataclass(frozen=True, slots=True)
class ClipSizeRow:
    """One clip size on the fee-vs-impact sweep."""

    size_usd: Decimal
    n: int
    mean_realised_bps: Decimal | None
    mean_realised_usd: Decimal | None
    median_realised_bps: Decimal | None
    win_rate: Decimal | None
    withdraw_fee_bps: Decimal  # flat fee as bps of size
    mean_fluxion_impact_bps: Decimal | None  # optional diagnostic

    def to_dict(self) -> dict[str, object]:
        def _s(v: Decimal | None) -> str | None:
            return None if v is None else str(v)

        return {
            "size_usd": str(self.size_usd),
            "n": self.n,
            "mean_realised_bps": _s(self.mean_realised_bps),
            "mean_realised_usd": _s(self.mean_realised_usd),
            "median_realised_bps": _s(self.median_realised_bps),
            "win_rate": _s(self.win_rate),
            "withdraw_fee_bps": str(self.withdraw_fee_bps),
            "mean_fluxion_impact_bps": _s(self.mean_fluxion_impact_bps),
        }


def _percentile(ordered: Sequence[Decimal], p: float) -> Decimal:
    """Nearest-rank percentile on a pre-sorted non-empty sequence."""
    if not ordered:
        raise ValueError("percentile on empty sequence")
    if len(ordered) == 1:
        return ordered[0]
    k = min(len(ordered) - 1, max(0, int(round(p * (len(ordered) - 1)))))
    return ordered[k]


def distribution_stats(values: Sequence[Decimal]) -> DistStats:
    """Mean / median / tails / win rate for a realised series."""
    if not values:
        return DistStats(
            n=0,
            mean=None,
            median=None,
            p5=None,
            p25=None,
            p75=None,
            p95=None,
            worst=None,
            best=None,
            win_rate=None,
            n_wins=0,
        )
    ordered = sorted(values)
    n = len(ordered)
    total = sum(ordered, start=Decimal(0))
    mean = total / Decimal(n)
    n_wins = sum(1 for v in ordered if v > 0)
    win_rate = Decimal(n_wins) / Decimal(n)
    return DistStats(
        n=n,
        mean=mean,
        median=_percentile(ordered, 0.5),
        p5=_percentile(ordered, 0.05),
        p25=_percentile(ordered, 0.25),
        p75=_percentile(ordered, 0.75),
        p95=_percentile(ordered, 0.95),
        worst=ordered[0],
        best=ordered[-1],
        win_rate=win_rate,
        n_wins=n_wins,
    )


def sample_std(values: Sequence[Decimal]) -> Decimal | None:
    """Sample standard deviation (ddof=1). None if n < 2."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values, start=Decimal(0)) / Decimal(n)
    # Use float for sqrt of variance; bps precision is fine at float64.
    acc = 0.0
    for v in values:
        d = float(v - mean)
        acc += d * d
    var = acc / (n - 1)
    return Decimal(str(math.sqrt(var)))


def log_return_bps(mid_t: Decimal, mid_later: Decimal) -> Decimal | None:
    """``1e4 * ln(mid_later / mid_t)`` in bps, or None if non-positive mids."""
    if mid_t <= 0 or mid_later <= 0:
        return None
    return Decimal(str(10_000.0 * math.log(float(mid_later) / float(mid_t))))


def transit_sigma_bps(
    mids: Sequence[tuple[int, Decimal]],
    *,
    lag_ms: int,
    max_align_ms: int,
) -> tuple[Decimal | None, int]:
    """1σ of Bybit mid log-returns over ``lag_ms``.

    ``mids`` must be sorted by timestamp ascending. For each mid at ``t``,
    take the rightmost mid with timestamp ≤ ``t + lag_ms`` whose age relative
    to the target is ≤ ``max_align_ms`` (as-of join with staleness gate).

    Returns ``(sigma_bps, n_returns)``.
    """
    if lag_ms <= 0:
        raise ValueError("lag_ms must be positive")
    if max_align_ms < 0:
        raise ValueError("max_align_ms must be non-negative")
    if len(mids) < 2:
        return None, 0

    ts_list = [t for t, _ in mids]
    rets: list[Decimal] = []

    for i, (t0, m0) in enumerate(mids):
        target = t0 + lag_ms
        j = bisect.bisect_right(ts_list, target) - 1
        if j < 0 or j == i:
            continue
        t1, m1 = mids[j]
        if target - t1 > max_align_ms:
            continue
        r = log_return_bps(m0, m1)
        if r is None:
            continue
        rets.append(r)
    return sample_std(rets), len(rets)


def fit_drift_premium_k(
    outcomes: Sequence[SequentialOutcome],
    *,
    sigma_bps: Decimal,
    targets: Sequence[Decimal] = DEFAULT_WIN_RATE_TARGETS,
    k_max: Decimal = Decimal("5"),
    k_step: Decimal = Decimal("0.1"),
    min_admitted: int = 5,
) -> list[DriftPremiumFit]:
    """Minimal k where admitted cycles hit each target realised win rate.

    Admission: ``simultaneous_edge_bps >= k * sigma_bps``. Scans k from 0 to
    ``k_max`` inclusive. Requires at least ``min_admitted`` samples at the
    chosen k (otherwise continue scanning upward); if none reach the target
    with enough samples, ``reachable=False`` and ``k`` is the best observed.
    """
    if sigma_bps < 0:
        raise ValueError("sigma_bps must be non-negative")
    fillable = [o for o in outcomes if o.fillable]
    fits: list[DriftPremiumFit] = []
    if not fillable or sigma_bps == 0:
        for t in targets:
            fits.append(
                DriftPremiumFit(
                    target_win_rate=t,
                    k=None,
                    n_admitted=len(fillable),
                    realised_win_rate=_win_rate([o.realised_pnl_usd for o in fillable]),
                    sigma_bps=sigma_bps,
                    reachable=False,
                )
            )
        return fits

    # Precompute k grid.
    ks: list[Decimal] = []
    k = Decimal(0)
    while k <= k_max + Decimal("1e-12"):
        ks.append(k)
        k += k_step

    for target in targets:
        best_k: Decimal | None = None
        best_n = 0
        best_wr: Decimal | None = None
        # Track highest win rate seen with enough samples (fallback).
        fallback_k: Decimal | None = None
        fallback_n = 0
        fallback_wr: Decimal | None = None

        for kk in ks:
            threshold = kk * sigma_bps
            admitted = [
                o for o in fillable if o.simultaneous_edge_bps >= threshold
            ]
            n = len(admitted)
            if n < min_admitted:
                continue
            wr = _win_rate([o.realised_pnl_usd for o in admitted])
            assert wr is not None
            if fallback_wr is None or wr > fallback_wr:
                fallback_wr = wr
                fallback_k = kk
                fallback_n = n
            if wr >= target:
                best_k = kk
                best_n = n
                best_wr = wr
                break  # minimal k that clears target

        if best_k is not None:
            fits.append(
                DriftPremiumFit(
                    target_win_rate=target,
                    k=best_k,
                    n_admitted=best_n,
                    realised_win_rate=best_wr,
                    sigma_bps=sigma_bps,
                    reachable=True,
                )
            )
        else:
            fits.append(
                DriftPremiumFit(
                    target_win_rate=target,
                    k=fallback_k,
                    n_admitted=fallback_n,
                    realised_win_rate=fallback_wr,
                    sigma_bps=sigma_bps,
                    reachable=False,
                )
            )
    return fits


def _win_rate(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    wins = sum(1 for v in values if v > 0)
    return Decimal(wins) / Decimal(len(values))


def withdraw_fee_bps(size_usd: Decimal, fee_usd: Decimal) -> Decimal:
    """Flat withdrawal fee expressed as bps of notional."""
    if size_usd <= 0:
        raise ValueError("size_usd must be positive")
    return fee_usd / size_usd * Decimal(10_000)


def clip_size_sweep(
    outcomes_by_size: dict[Decimal, Sequence[SequentialOutcome]],
    *,
    withdraw_fee_usd: Decimal,
    impact_bps_by_size: dict[Decimal, Decimal] | None = None,
) -> list[ClipSizeRow]:
    """Summarise realised PnL per clip size (fee vs impact trade-off)."""
    rows: list[ClipSizeRow] = []
    for size in sorted(outcomes_by_size.keys()):
        outs = [o for o in outcomes_by_size[size] if o.fillable]
        bps_vals = [o.realised_bps for o in outs]
        usd_vals = [o.realised_pnl_usd for o in outs]
        bps_stats = distribution_stats(bps_vals)
        usd_stats = distribution_stats(usd_vals)
        impact = None
        if impact_bps_by_size is not None and size in impact_bps_by_size:
            impact = impact_bps_by_size[size]
        rows.append(
            ClipSizeRow(
                size_usd=size,
                n=bps_stats.n,
                mean_realised_bps=bps_stats.mean,
                mean_realised_usd=usd_stats.mean,
                median_realised_bps=bps_stats.median,
                win_rate=bps_stats.win_rate,
                withdraw_fee_bps=withdraw_fee_bps(size, withdraw_fee_usd),
                mean_fluxion_impact_bps=impact,
            )
        )
    return rows


def optimal_clip_size(rows: Sequence[ClipSizeRow]) -> ClipSizeRow | None:
    """Pick the clip size with highest **positive** mean realised USD.

    Sizes with n=0 or non-positive mean USD are ignored — a losing clip is not
    an optimum. Ties break toward larger size.
    """
    candidates = [
        r
        for r in rows
        if r.n > 0
        and r.mean_realised_usd is not None
        and r.mean_realised_usd > 0
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda r: (r.mean_realised_usd or Decimal(0), r.size_usd),
    )


def sequential_capturable_profit(
    outcomes: Sequence[SequentialOutcome],
    *,
    reentry_cooldown_ms: int,
    trade_duration_ms: int,
    min_simultaneous_edge_bps: Decimal = Decimal(0),
) -> Decimal:
    """Single-flight sum of **realised** PnL on admitted fire-on-open cycles.

    Outcomes must be one (pair, size, lag) series. Admission uses simultaneous
    edge; booked PnL is realised. Only positive realised fills contribute
    (same spirit as edge_quant capturable profit).

    ``reentry_cooldown_ms`` spaces successive entries on this series
    (``trade_duration_ms + reentry_cooldown_ms`` after an entry). With the
    study default of a 1-day cooldown this is effectively one cycle per day.
    """
    admitted = sorted(
        (
            o
            for o in outcomes
            if o.fillable and o.simultaneous_edge_bps >= min_simultaneous_edge_bps
        ),
        key=lambda o: o.entry_ts_ms,
    )
    if not admitted:
        return Decimal(0)
    profit = Decimal(0)
    next_free = 0
    step = max(1, trade_duration_ms + max(0, reentry_cooldown_ms))
    for o in admitted:
        if o.entry_ts_ms < next_free:
            continue
        if o.realised_pnl_usd > 0:
            profit += o.realised_pnl_usd
        next_free = o.entry_ts_ms + step
    return profit


def portfolio_sequential_profit(
    outcomes: Sequence[SequentialOutcome],
    *,
    trade_duration_ms: int,
    min_simultaneous_edge_bps: Decimal = Decimal(0),
) -> Decimal:
    """Cross-symbol single-flight on realised PnL (one cycle at a time).

    At each free slot, among remaining admitted outcomes still available,
    pick the highest positive realised PnL and consume it.
    """
    remaining = [
        o
        for o in outcomes
        if o.fillable
        and o.simultaneous_edge_bps >= min_simultaneous_edge_bps
        and o.realised_pnl_usd > 0
    ]
    if not remaining:
        return Decimal(0)
    remaining.sort(key=lambda o: o.entry_ts_ms)
    profit = Decimal(0)
    next_free = 0
    flight = max(1, trade_duration_ms)
    # Greedy: walk time, at each free moment take best available entry.
    # Bound iterations by n.
    for _ in range(len(remaining) + 1):
        candidates = [o for o in remaining if o.entry_ts_ms >= next_free]
        if not candidates:
            break
        # Earliest free time among candidates that can enter now.
        earliest = min(o.entry_ts_ms for o in candidates)
        at_earliest = [o for o in candidates if o.entry_ts_ms == earliest]
        best = max(at_earliest, key=lambda o: o.realised_pnl_usd)
        profit += best.realised_pnl_usd
        next_free = earliest + flight
        remaining = [o for o in remaining if o is not best]
    return profit


__all__ = [
    "DEFAULT_WIN_RATE_TARGETS",
    "ClipSizeRow",
    "DistStats",
    "DriftPremiumFit",
    "SequentialOutcome",
    "clip_size_sweep",
    "distribution_stats",
    "fit_drift_premium_k",
    "log_return_bps",
    "optimal_clip_size",
    "portfolio_sequential_profit",
    "sample_std",
    "sequential_capturable_profit",
    "transit_sigma_bps",
    "withdraw_fee_bps",
]
