"""Cumulative distributions and cost-floor breach accounting (WHI-732).

Percentiles are **time-weighted**: each sample carries weight equal to the
forward gap until the next sample (capped by ``max_breach_gap_ms``), matching
DESIGN §2.4 "time-weighted stats". Breach duration uses the same gap rule so
overnight / reconnect windows do not inflate open-session totals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from monitor.metrics.config import MetricsConfig
from monitor.metrics.edge import EdgeResult
from monitor.metrics.session import SessionKind


@dataclass(frozen=True, slots=True)
class Distribution:
    """Time-weighted percentiles over a numeric series (bps or similar)."""

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
        """Equal-weight convenience (tests / ad-hoc). Prefer weighted path."""
        if not values:
            return cls.empty()
        weights = [Decimal(1)] * len(values)
        return cls.from_weighted(values, weights)

    @classmethod
    def from_weighted(
        cls, values: list[Decimal], weights: list[Decimal]
    ) -> Distribution:
        if not values:
            return cls.empty()
        if len(values) != len(weights):
            raise ValueError("values/weights length mismatch")
        pairs = sorted(zip(values, weights, strict=True), key=lambda p: p[0])
        total_w = sum((w for _, w in pairs), Decimal(0))
        if total_w <= 0:
            # fall back to equal weight
            return cls.from_values(values)
        return cls(
            count=len(values),
            p50=_weighted_percentile(pairs, total_w, 50),
            p95=_weighted_percentile(pairs, total_w, 95),
            p99=_weighted_percentile(pairs, total_w, 99),
            max=pairs[-1][0],
        )


def _weighted_percentile(
    sorted_pairs: list[tuple[Decimal, Decimal]],
    total_w: Decimal,
    pct: int,
) -> Decimal:
    """First value where cumulative weight reaches pct% of total."""
    if not sorted_pairs:
        raise ValueError("empty")
    if pct <= 0:
        return sorted_pairs[0][0]
    if pct >= 100:
        return sorted_pairs[-1][0]
    target = total_w * Decimal(pct) / Decimal(100)
    cum = Decimal(0)
    for value, weight in sorted_pairs:
        cum += weight
        if cum >= target:
            return value
    return sorted_pairs[-1][0]


@dataclass(frozen=True, slots=True)
class BreachStats:
    """Cost-floor breach episodes for one series.

    A breach is a contiguous run of samples where ``net_edge_bps > 0`` (and
    fillable) at ``config.breach_size_usd``. Duration sums inter-sample gaps
    while breaching, each gap capped by ``max_breach_gap_ms``.
    """

    episode_count: int
    total_duration_ms: int
    currently_breaching: bool


@dataclass
class _SeriesState:
    # (value, weight_ms) — weight is the *forward* gap to the next sample.
    samples: list[tuple[Decimal, Decimal]] = field(default_factory=list)
    last_ts_ms: int | None = None
    last_value: Decimal | None = None
    last_breaching: bool = False
    episode_count: int = 0
    total_duration_ms: int = 0


@dataclass
class EdgeStats:
    """Running cumulative stats, optionally split by session."""

    max_gap_ms: int = 300_000
    _all: _SeriesState = field(default_factory=_SeriesState)
    _open: _SeriesState = field(default_factory=_SeriesState)
    _closed: _SeriesState = field(default_factory=_SeriesState)

    @classmethod
    def from_config(cls, config: MetricsConfig) -> EdgeStats:
        return cls(max_gap_ms=config.max_breach_gap_ms)

    def observe(
        self,
        *,
        net_edge_bps: Decimal,
        ts_ms: int,
        session: SessionKind,
        fillable: bool = True,
    ) -> None:
        for state in (self._all, self._bucket(session)):
            self._observe_one(
                state,
                net_edge_bps=net_edge_bps,
                ts_ms=ts_ms,
                fillable=fillable,
            )

    def observe_edge(
        self,
        edge: EdgeResult,
        *,
        ts_ms: int,
        session: SessionKind,
        config: MetricsConfig,
    ) -> None:
        """Record only when ``edge.size_usd`` matches ``config.breach_size_usd``.

        Other ladder sizes are ignored so the configured cost floor is the one
        that drives breach episode accounting.
        """
        if edge.size_usd != config.breach_size_usd:
            return
        self.observe(
            net_edge_bps=edge.net_edge_bps,
            ts_ms=ts_ms,
            session=session,
            fillable=edge.fillable,
        )

    def _bucket(self, session: SessionKind) -> _SeriesState:
        return self._open if session is SessionKind.OPEN else self._closed

    def _observe_one(
        self,
        state: _SeriesState,
        *,
        net_edge_bps: Decimal,
        ts_ms: int,
        fillable: bool,
    ) -> None:
        gap_ms = 0
        if state.last_ts_ms is not None and ts_ms >= state.last_ts_ms:
            raw = ts_ms - state.last_ts_ms
            # Cap: overnight / reconnect must not inflate duration or weights.
            gap_ms = raw if raw <= self.max_gap_ms else 0
            if state.last_breaching and gap_ms > 0:
                state.total_duration_ms += gap_ms
            # Assign the forward weight to the *previous* sample.
            if state.samples and state.last_value is not None:
                prev_val, _ = state.samples[-1]
                # Replace last sample's weight with the realized forward gap
                # (0 if gap was capped out — sample stays for max, not Pxx).
                weight = Decimal(gap_ms) if gap_ms > 0 else Decimal(0)
                state.samples[-1] = (prev_val, weight)

        state.samples.append((net_edge_bps, Decimal(0)))  # weight filled later
        breaching = fillable and net_edge_bps > 0
        if breaching and not state.last_breaching:
            state.episode_count += 1
        state.last_breaching = breaching
        state.last_ts_ms = ts_ms
        state.last_value = net_edge_bps

    def distribution(self, session: SessionKind | None = None) -> Distribution:
        st = self._state(session)
        if not st.samples:
            return Distribution.empty()
        values = [v for v, _ in st.samples]
        weights = [w if w > 0 else Decimal(1) for _, w in st.samples]
        return Distribution.from_weighted(values, weights)

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
