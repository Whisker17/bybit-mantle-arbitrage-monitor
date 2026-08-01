"""Cumulative distributions and cost-floor breach accounting (WHI-732).

All samples are optionally tagged with session open/closed so aggregates can be
segmented (DESIGN §2.4). Time-weighted breach duration uses the wall-clock gap
between successive samples of the same series key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from monitor.metrics.session import SessionKind


@dataclass(frozen=True, slots=True)
class Distribution:
    """Percentiles over a numeric series (bps or similar)."""

    count: int
    p50: Decimal | None
    p95: Decimal | None
    p99: Decimal | None
    max: Decimal | None

    @classmethod
    def empty(cls) -> Distribution:
        return cls(count=0, p50=None, p95=None, p99=None, max=None)

    @classmethod
    def from_values(cls, values: list[Decimal]) -> Distribution:
        if not values:
            return cls.empty()
        ordered = sorted(values)
        n = len(ordered)
        return cls(
            count=n,
            p50=_percentile(ordered, 50),
            p95=_percentile(ordered, 95),
            p99=_percentile(ordered, 99),
            max=ordered[-1],
        )


def _percentile(sorted_vals: list[Decimal], pct: int) -> Decimal:
    """Nearest-rank percentile on a non-empty sorted list."""
    if not sorted_vals:
        raise ValueError("empty")
    if pct <= 0:
        return sorted_vals[0]
    if pct >= 100:
        return sorted_vals[-1]
    # nearest rank: ceil(p/100 * n), 1-indexed
    n = len(sorted_vals)
    rank = (pct * n + 99) // 100  # ceil(pct/100 * n)
    rank = max(1, min(n, rank))
    return sorted_vals[rank - 1]


@dataclass(frozen=True, slots=True)
class BreachStats:
    """Cost-floor breach episodes for one series.

    A breach is a contiguous run of samples where ``net_edge_bps > 0`` (and
    fillable). Duration is the sum of inter-sample gaps (ms) while breached;
    the final open episode uses time through the last sample only.
    """

    episode_count: int
    total_duration_ms: int
    currently_breaching: bool


@dataclass
class _SeriesState:
    values: list[Decimal] = field(default_factory=list)
    last_ts_ms: int | None = None
    last_breaching: bool = False
    episode_count: int = 0
    total_duration_ms: int = 0


@dataclass
class EdgeStats:
    """Running cumulative stats, optionally split by session."""

    _all: _SeriesState = field(default_factory=_SeriesState)
    _open: _SeriesState = field(default_factory=_SeriesState)
    _closed: _SeriesState = field(default_factory=_SeriesState)

    def observe(
        self,
        *,
        net_edge_bps: Decimal,
        ts_ms: int,
        session: SessionKind,
        fillable: bool = True,
    ) -> None:
        """Record one sample. Non-fillable samples skip breach accounting but
        still enter the distribution (as the observed net after wear)."""
        for state in (self._all, self._bucket(session)):
            self._observe_one(
                state,
                net_edge_bps=net_edge_bps,
                ts_ms=ts_ms,
                fillable=fillable,
            )

    def _bucket(self, session: SessionKind) -> _SeriesState:
        return self._open if session is SessionKind.OPEN else self._closed

    @staticmethod
    def _observe_one(
        state: _SeriesState,
        *,
        net_edge_bps: Decimal,
        ts_ms: int,
        fillable: bool,
    ) -> None:
        state.values.append(net_edge_bps)
        breaching = fillable and net_edge_bps > 0
        if state.last_ts_ms is not None and ts_ms >= state.last_ts_ms:
            dt = ts_ms - state.last_ts_ms
            if state.last_breaching:
                state.total_duration_ms += dt
        if breaching and not state.last_breaching:
            state.episode_count += 1
        state.last_breaching = breaching
        state.last_ts_ms = ts_ms

    def distribution(self, session: SessionKind | None = None) -> Distribution:
        return Distribution.from_values(self._state(session).values)

    def breach_stats(self, session: SessionKind | None = None) -> BreachStats:
        st = self._state(session)
        return BreachStats(
            episode_count=st.episode_count,
            total_duration_ms=st.total_duration_ms,
            currently_breaching=st.last_breaching,
        )

    def _state(self, session: SessionKind | None) -> _SeriesState:
        if session is None:
            return self._all
        return self._open if session is SessionKind.OPEN else self._closed


@dataclass(frozen=True, slots=True)
class SessionBuckets:
    """Convenience snapshot of open/closed distributions + breaches."""

    all: Distribution
    open: Distribution
    closed: Distribution
    breach_all: BreachStats
    breach_open: BreachStats
    breach_closed: BreachStats

    @classmethod
    def from_stats(cls, stats: EdgeStats) -> SessionBuckets:
        return cls(
            all=stats.distribution(None),
            open=stats.distribution(SessionKind.OPEN),
            closed=stats.distribution(SessionKind.CLOSED),
            breach_all=stats.breach_stats(None),
            breach_open=stats.breach_stats(SessionKind.OPEN),
            breach_closed=stats.breach_stats(SessionKind.CLOSED),
        )
