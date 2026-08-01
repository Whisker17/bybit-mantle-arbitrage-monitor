"""Pair / session aggregates for M5 secondary pages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from monitor.attribution.config import AttributionConfig
from monitor.attribution.convergence import convergence_share
from monitor.attribution.events import AmmTradeEvent, RfqFillEvent
from monitor.attribution.labels import (
    ActivityRegime,
    BehaviorLabel,
    TakerProfile,
    label_takers,
)
from monitor.attribution.mechanism import (
    Mechanism,
    mechanism_for_rfq_fill,
    mechanism_for_swap,
)
from monitor.metrics.session import SessionKind

SessionFilter = SessionKind | Literal["all"]


@dataclass(frozen=True, slots=True)
class MechanismShare:
    amm_trades: int
    rfq_trades: int

    @property
    def total(self) -> int:
        return self.amm_trades + self.rfq_trades

    @property
    def rfq_share(self) -> float | None:
        if self.total <= 0:
            return None
        return self.rfq_trades / self.total

    @property
    def amm_share(self) -> float | None:
        if self.total <= 0:
            return None
        return self.amm_trades / self.total


@dataclass(frozen=True, slots=True)
class TakerRow:
    """One top-taker row for the M5 detail panel."""

    address: str
    is_contract: bool | None
    role: str | None
    label: BehaviorLabel
    n_trades: int
    notional_usd: Decimal
    trades_per_day: float | None
    convergence_ratio: float | None
    bybit_align_ratio: float | None
    open_share: float
    closed_share: float
    activity_regime: ActivityRegime
    n_buy: int
    n_sell: int

    @classmethod
    def from_profile(cls, profile: TakerProfile) -> TakerRow:
        f = profile.features
        return cls(
            address=f.address,
            is_contract=f.is_contract,
            role=f.role,
            label=profile.label,
            n_trades=f.n_trades,
            notional_usd=f.notional_usd,
            trades_per_day=f.trades_per_day,
            convergence_ratio=f.convergence_ratio,
            bybit_align_ratio=f.bybit_align_ratio,
            open_share=f.open_share,
            closed_share=f.closed_share,
            activity_regime=f.activity_regime,
            n_buy=f.n_buy,
            n_sell=f.n_sell,
        )


@dataclass(frozen=True, slots=True)
class PairAttribution:
    """Attribution panel model for one pair (and optional session slice).

    Consumable directly by M5 secondary pages — pure data, no I/O.
    """

    pair_id: str
    session: SessionFilter
    window_start_ms: int | None
    window_end_ms: int | None
    mechanism: MechanismShare
    # Share of AMM trades (with scorable mids) that converge toward Bybit.
    convergence_share: float | None
    n_convergence_scored: int
    # Fraction of AMM trades whose *taker* carries each behavior label.
    label_trade_share: dict[BehaviorLabel, float]
    label_trade_counts: dict[BehaviorLabel, int]
    top_takers: list[TakerRow]
    # All labeled profiles (not truncated); useful for offline QA.
    takers: list[TakerProfile]


def _filter_session(
    trades: Sequence[AmmTradeEvent],
    session: SessionFilter,
) -> list[AmmTradeEvent]:
    if session == "all":
        return list(trades)
    return [t for t in trades if t.session is session]


def window_bounds_ms(
    amm: Sequence[AmmTradeEvent],
    rfq: Sequence[RfqFillEvent] = (),
) -> tuple[int | None, int | None]:
    """Inclusive [min, max] wall-clock ms over event timestamps, or (None, None)."""
    stamps: list[int] = [t.ts_ms for t in amm] + [f.ts_ms for f in rfq]
    if not stamps:
        return None, None
    return min(stamps), max(stamps)


def mechanism_share(
    amm_trades: Sequence[AmmTradeEvent],
    rfq_fills: Sequence[RfqFillEvent],
) -> MechanismShare:
    return MechanismShare(amm_trades=len(amm_trades), rfq_trades=len(rfq_fills))


def label_trade_counts(
    trades: Sequence[AmmTradeEvent],
    labels: Mapping[str, BehaviorLabel],
) -> dict[BehaviorLabel, int]:
    counts: dict[BehaviorLabel, int] = {lab: 0 for lab in BehaviorLabel}
    for t in trades:
        lab = labels.get(t.taker.lower(), BehaviorLabel.UNKNOWN)
        counts[lab] += 1
    return counts


def label_trade_share(
    counts: Mapping[BehaviorLabel, int],
) -> dict[BehaviorLabel, float]:
    total = sum(counts.values())
    if total <= 0:
        return {lab: 0.0 for lab in BehaviorLabel}
    return {lab: counts.get(lab, 0) / total for lab in BehaviorLabel}


def build_pair_attribution(
    *,
    pair_id: str,
    amm_trades: Sequence[AmmTradeEvent],
    rfq_fills: Sequence[RfqFillEvent] = (),
    config: AttributionConfig,
    session: SessionFilter = "all",
    contract_flags: Mapping[str, bool] | None = None,
    roles: Mapping[str, str] | None = None,
) -> PairAttribution:
    """Aggregate mechanism + behavior stats for one pair.

    ``amm_trades`` / ``rfq_fills`` should already be scoped to ``pair_id``
    (RFQ fills with ``pair_id is None`` are ignored for pair views — use
    ``build_global_mechanism_share`` for unscoped RFQ counts).
    Time-period bucketing is the caller's window plus ``session`` filter
    (open / closed / all); there is no internal multi-bucket rollup.
    """
    amm = [t for t in amm_trades if t.pair_id == pair_id]
    amm = _filter_session(amm, session)
    # Pair-scoped: only fills explicitly tagged with this pair_id (unscoped
    # RFQ fills contribute to global mechanism share only — see
    # docs/DEFERRED_ISSUES.md RFQ fill enrichment).
    rfq = [f for f in rfq_fills if f.pair_id == pair_id]

    profiles = label_takers(
        amm, config, contract_flags=contract_flags, roles=roles
    )
    labels = {p.address: p.label for p in profiles}
    counts = label_trade_counts(amm, labels)
    conv, n_conv = convergence_share(amm)
    top_n = config.top_takers_n
    top = [TakerRow.from_profile(p) for p in profiles[:top_n]]
    start, end = window_bounds_ms(amm, rfq)

    return PairAttribution(
        pair_id=pair_id,
        session=session,
        window_start_ms=start,
        window_end_ms=end,
        mechanism=mechanism_share(amm, rfq),
        convergence_share=conv,
        n_convergence_scored=n_conv,
        label_trade_share=label_trade_share(counts),
        label_trade_counts=counts,
        top_takers=top,
        takers=profiles,
    )


def build_global_mechanism_share(
    amm_trades: Sequence[AmmTradeEvent],
    rfq_fills: Sequence[RfqFillEvent],
) -> MechanismShare:
    """RFQ vs AMM across all pairs (includes unscoped RFQ fills)."""
    return mechanism_share(amm_trades, rfq_fills)


def mechanism_of_trade(trade: AmmTradeEvent | RfqFillEvent) -> Mechanism:
    """Mechanism for a normalized fill event (constant per event type)."""
    if isinstance(trade, AmmTradeEvent):
        return mechanism_for_swap(trade)
    return mechanism_for_rfq_fill(trade)
