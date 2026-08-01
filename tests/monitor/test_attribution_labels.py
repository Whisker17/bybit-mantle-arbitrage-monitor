"""Seam: assign_behavior_label / label_takers / activity_regime."""

from __future__ import annotations

from decimal import Decimal

from monitor.attribution import (
    ActivityRegime,
    AddressFeatures,
    BehaviorLabel,
    assign_behavior_label,
    compute_address_features,
    label_takers,
    load_attribution_config,
)
from monitor.attribution.events import AmmTradeEvent
from monitor.metrics.session import SessionKind


def _cfg():
    return load_attribution_config()


def _trade(
    *,
    taker: str = "0x" + "aa" * 20,
    direction: str = "buy_native",
    notional: str = "100",
    fluxion: str | None = "95",
    bybit: str | None = "100",
    bybit_prev: str | None = "99",
    session: SessionKind = SessionKind.OPEN,
    ts_ms: int = 1_700_000_000_000,
    log_index: int = 0,
) -> AmmTradeEvent:
    return AmmTradeEvent(
        pair_id="AAPLx",
        ts_ms=ts_ms + log_index,
        block_number=1 + log_index,
        tx_hash=f"0x{log_index:064x}",
        log_index=log_index,
        taker=taker.lower(),
        sender="0x" + "bb" * 20,
        direction=direction,  # type: ignore[arg-type]
        notional_usd=Decimal(notional),
        fluxion_mid_pre=Decimal(fluxion) if fluxion is not None else None,
        bybit_mid=Decimal(bybit) if bybit is not None else None,
        bybit_mid_prev=Decimal(bybit_prev) if bybit_prev is not None else None,
        session=session,
    )


def _feats(**overrides: object) -> AddressFeatures:
    base: dict[str, object] = {
        "address": "0x" + "aa" * 20,
        "n_trades": 25,
        "n_buy": 20,
        "n_sell": 5,
        "notional_usd": Decimal(2500),
        "median_notional_usd": Decimal(100),
        "max_notional_usd": Decimal(200),
        "open_share": 0.5,
        "closed_share": 0.5,
        "activity_regime": ActivityRegime.ALL_HOURS,
        "convergence_ratio": 0.90,
        "n_convergence_scored": 25,
        "bybit_align_ratio": None,
        "n_bybit_align_scored": 0,
        "is_contract": True,
    }
    base.update(overrides)
    return AddressFeatures(**base)  # type: ignore[arg-type]


def test_arb_bot_at_80pct_with_20_trades() -> None:
    cfg = _cfg()
    assert assign_behavior_label(_feats(), cfg) is BehaviorLabel.ARB_BOT


def test_arb_bot_fails_below_min_trades() -> None:
    cfg = _cfg()
    assert (
        assign_behavior_label(_feats(n_trades=19, convergence_ratio=1.0), cfg)
        is not BehaviorLabel.ARB_BOT
    )


def test_arb_bot_fails_below_convergence() -> None:
    cfg = _cfg()
    assert (
        assign_behavior_label(_feats(convergence_ratio=0.79), cfg)
        is not BehaviorLabel.ARB_BOT
    )


def test_price_keeper_small_bidirectional() -> None:
    cfg = _cfg()
    # Not arb (low convergence) but bidirectional small notional.
    f = _feats(
        n_trades=12,
        n_buy=6,
        n_sell=6,
        convergence_ratio=0.4,
        median_notional_usd=Decimal(50),
        max_notional_usd=Decimal(100),
    )
    assert assign_behavior_label(f, cfg) is BehaviorLabel.PRICE_KEEPER


def test_price_keeper_rejects_large_trade() -> None:
    cfg = _cfg()
    f = _feats(
        n_trades=12,
        n_buy=6,
        n_sell=6,
        convergence_ratio=0.4,
        median_notional_usd=Decimal(50),
        max_notional_usd=Decimal(5000),
    )
    assert assign_behavior_label(f, cfg) is BehaviorLabel.RETAIL


def test_retail_when_enough_samples_no_bot_sig() -> None:
    cfg = _cfg()
    f = _feats(
        n_trades=8,
        n_buy=8,
        n_sell=0,
        convergence_ratio=0.5,
        median_notional_usd=Decimal(1000),
        max_notional_usd=Decimal(2000),
    )
    assert assign_behavior_label(f, cfg) is BehaviorLabel.RETAIL


def test_unknown_when_thin_sample() -> None:
    cfg = _cfg()
    f = _feats(n_trades=2, n_buy=2, n_sell=0, convergence_ratio=1.0)
    assert assign_behavior_label(f, cfg) is BehaviorLabel.UNKNOWN


def test_arb_bot_priority_over_price_keeper() -> None:
    cfg = _cfg()
    # Would match price-keeper shape but high convergence + enough trades → arb.
    f = _feats(
        n_trades=25,
        n_buy=13,
        n_sell=12,
        convergence_ratio=0.88,
        median_notional_usd=Decimal(40),
        max_notional_usd=Decimal(80),
    )
    assert assign_behavior_label(f, cfg) is BehaviorLabel.ARB_BOT


def test_compute_features_convergence_and_regime() -> None:
    cfg = _cfg()
    taker = "0x" + "cc" * 20
    # 10 converging buys while Fluxion < Bybit, mix of sessions.
    trades = [
        _trade(
            taker=taker,
            direction="buy_native",
            fluxion="95",
            bybit="100",
            session=SessionKind.OPEN if i < 8 else SessionKind.CLOSED,
            log_index=i,
        )
        for i in range(10)
    ]
    feats = compute_address_features(trades, address=taker, config=cfg, is_contract=False)
    assert feats.n_trades == 10
    assert feats.n_buy == 10
    assert feats.convergence_ratio == 1.0
    assert feats.closed_share == 0.2
    assert feats.activity_regime is ActivityRegime.ALL_HOURS
    assert feats.is_contract is False


def test_label_takers_orders_by_count() -> None:
    cfg = _cfg()
    a = "0x" + "a1" * 20
    b = "0x" + "b2" * 20
    trades = [_trade(taker=a, log_index=i) for i in range(6)]
    trades += [_trade(taker=b, log_index=100 + i) for i in range(3)]
    profiles = label_takers(trades, cfg, contract_flags={a: True, b: False})
    assert [p.address for p in profiles] == [a, b]
    assert profiles[0].label is BehaviorLabel.RETAIL
    assert profiles[0].features.is_contract is True
    assert profiles[1].label is BehaviorLabel.UNKNOWN
