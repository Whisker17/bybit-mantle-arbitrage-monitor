"""Behavior labels for AMM taker addresses (arb-bot / price-keeper / retail / unknown)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from statistics import median

from monitor.attribution.config import AttributionConfig
from monitor.attribution.convergence import bybit_move_aligned, trade_convergence_flag
from monitor.attribution.events import AmmTradeEvent
from monitor.metrics.session import SessionKind


class BehaviorLabel(StrEnum):
    ARB_BOT = "arb_bot"
    PRICE_KEEPER = "price_keeper"
    RETAIL = "retail"
    UNKNOWN = "unknown"


class ActivityRegime(StrEnum):
    ALL_HOURS = "all_hours"
    RTH_ONLY = "rth_only"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AddressFeatures:
    """Per-address features inside one (pair, window[, session]) scope."""

    address: str
    n_trades: int
    n_buy: int
    n_sell: int
    notional_usd: Decimal
    median_notional_usd: Decimal
    max_notional_usd: Decimal
    open_share: float
    closed_share: float
    activity_regime: ActivityRegime
    convergence_ratio: float | None
    n_convergence_scored: int
    bybit_align_ratio: float | None
    n_bybit_align_scored: int
    is_contract: bool | None


@dataclass(frozen=True, slots=True)
class TakerProfile:
    """Features + assigned behavior label (M5 top-taker row)."""

    features: AddressFeatures
    label: BehaviorLabel

    @property
    def address(self) -> str:
        return self.features.address


def _ratio(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return num / den


def activity_regime(
    *,
    n_trades: int,
    closed_share: float,
    config: AttributionConfig,
) -> ActivityRegime:
    act = config.activity
    if n_trades < act.min_trades_for_regime:
        return ActivityRegime.UNKNOWN
    if closed_share >= act.min_closed_share_for_all_hours:
        return ActivityRegime.ALL_HOURS
    return ActivityRegime.RTH_ONLY


def assign_behavior_label(
    features: AddressFeatures,
    config: AttributionConfig,
) -> BehaviorLabel:
    """Priority: arb_bot → price_keeper → retail → unknown.

    Rules documented in docs/references/m4-attribution-labels.md.
    """
    arb = config.arb_bot
    if (
        features.n_trades >= arb.min_trades
        and features.convergence_ratio is not None
        and features.convergence_ratio >= arb.min_convergence_ratio
    ):
        align_ok = (
            features.bybit_align_ratio is None
            or features.bybit_align_ratio >= arb.min_bybit_align_ratio
        )
        if align_ok:
            return BehaviorLabel.ARB_BOT

    pk = config.price_keeper
    if features.n_trades >= pk.min_trades:
        buy_share = features.n_buy / features.n_trades
        sell_share = features.n_sell / features.n_trades
        if (
            buy_share >= pk.min_direction_share
            and sell_share >= pk.min_direction_share
            and features.median_notional_usd <= pk.max_median_notional_usd
            and features.max_notional_usd <= pk.max_trade_notional_usd
        ):
            return BehaviorLabel.PRICE_KEEPER

    if features.n_trades >= config.retail.min_trades:
        return BehaviorLabel.RETAIL
    return BehaviorLabel.UNKNOWN


def compute_address_features(
    trades: Sequence[AmmTradeEvent],
    *,
    address: str,
    config: AttributionConfig,
    is_contract: bool | None = None,
) -> AddressFeatures:
    """Aggregate features for one taker from their AMM trades."""
    addr = address.lower()
    rows = [t for t in trades if t.taker.lower() == addr]
    n = len(rows)
    if n == 0:
        return AddressFeatures(
            address=addr,
            n_trades=0,
            n_buy=0,
            n_sell=0,
            notional_usd=Decimal(0),
            median_notional_usd=Decimal(0),
            max_notional_usd=Decimal(0),
            open_share=0.0,
            closed_share=0.0,
            activity_regime=ActivityRegime.UNKNOWN,
            convergence_ratio=None,
            n_convergence_scored=0,
            bybit_align_ratio=None,
            n_bybit_align_scored=0,
            is_contract=is_contract,
        )

    n_buy = sum(1 for t in rows if t.direction == "buy_native")
    n_sell = sum(1 for t in rows if t.direction == "sell_native")
    notionals = [t.notional_usd for t in rows]
    notional_sum = sum(notionals, start=Decimal(0))
    med = Decimal(str(median([float(x) for x in notionals])))
    max_n = max(notionals)

    n_open = sum(1 for t in rows if t.session is SessionKind.OPEN)
    n_closed = sum(1 for t in rows if t.session is SessionKind.CLOSED)
    open_share = n_open / n
    closed_share = n_closed / n
    regime = activity_regime(n_trades=n, closed_share=closed_share, config=config)

    conv_hits = 0
    conv_scored = 0
    for t in rows:
        flag = trade_convergence_flag(
            t.direction, t.fluxion_mid_pre, t.bybit_mid
        )
        if flag is None:
            continue
        conv_scored += 1
        if flag:
            conv_hits += 1

    align_hits = 0
    align_scored = 0
    min_move = config.bybit_correlation.min_move_bps
    for t in rows:
        if t.bybit_mid is None or t.bybit_mid_prev is None:
            continue
        flag = bybit_move_aligned(
            t.direction,
            bybit_mid=t.bybit_mid,
            bybit_mid_prev=t.bybit_mid_prev,
            min_move_bps=min_move,
        )
        if flag is None:
            continue
        align_scored += 1
        if flag:
            align_hits += 1

    return AddressFeatures(
        address=addr,
        n_trades=n,
        n_buy=n_buy,
        n_sell=n_sell,
        notional_usd=notional_sum,
        median_notional_usd=med,
        max_notional_usd=max_n,
        open_share=open_share,
        closed_share=closed_share,
        activity_regime=regime,
        convergence_ratio=_ratio(conv_hits, conv_scored),
        n_convergence_scored=conv_scored,
        bybit_align_ratio=_ratio(align_hits, align_scored),
        n_bybit_align_scored=align_scored,
        is_contract=is_contract,
    )


def label_takers(
    trades: Sequence[AmmTradeEvent],
    config: AttributionConfig,
    *,
    contract_flags: Mapping[str, bool] | None = None,
) -> list[TakerProfile]:
    """Build sorted taker profiles (trade count desc, then address) for a trade set."""
    flags = {k.lower(): v for k, v in (contract_flags or {}).items()}
    by_addr: dict[str, list[AmmTradeEvent]] = defaultdict(list)
    for t in trades:
        by_addr[t.taker.lower()].append(t)

    profiles: list[TakerProfile] = []
    for addr, rows in by_addr.items():
        feats = compute_address_features(
            rows,
            address=addr,
            config=config,
            is_contract=flags.get(addr),
        )
        label = assign_behavior_label(feats, config)
        profiles.append(TakerProfile(features=feats, label=label))

    profiles.sort(key=lambda p: (-p.features.n_trades, p.address))
    return profiles


def label_lookup(profiles: Iterable[TakerProfile]) -> dict[str, BehaviorLabel]:
    return {p.address: p.label for p in profiles}
