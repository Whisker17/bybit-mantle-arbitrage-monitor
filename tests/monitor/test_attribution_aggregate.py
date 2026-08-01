"""Seam: build_pair_attribution / build_attribution_snapshot (M5 model)."""

from __future__ import annotations

from decimal import Decimal

from monitor.attribution import (
    BehaviorLabel,
    Mechanism,
    build_attribution_snapshot,
    build_global_mechanism_share,
    build_pair_attribution,
    load_attribution_config,
    mechanism_of_trade,
)
from monitor.attribution.events import AmmTradeEvent, RfqFillEvent
from monitor.metrics.session import SessionKind


def _cfg():
    return load_attribution_config()


def _amm(
    *,
    pair: str = "AAPLx",
    taker: str = "0x" + "aa" * 20,
    direction: str = "buy_native",
    fluxion: str = "95",
    bybit: str = "100",
    session: SessionKind = SessionKind.OPEN,
    notional: str = "100",
    i: int = 0,
) -> AmmTradeEvent:
    return AmmTradeEvent(
        pair_id=pair,
        ts_ms=1_700_000_000_000 + i,
        block_number=1000 + i,
        tx_hash=f"0x{i:064x}",
        log_index=i,
        taker=taker.lower(),
        sender="0x" + "bb" * 20,
        direction=direction,  # type: ignore[arg-type]
        notional_usd=Decimal(notional),
        fluxion_mid_pre=Decimal(fluxion),
        bybit_mid=Decimal(bybit),
        bybit_mid_prev=Decimal("99"),
        session=session,
    )


def _rfq(*, pair: str | None = "AAPLx", i: int = 0) -> RfqFillEvent:
    return RfqFillEvent(
        ts_ms=1_700_000_100_000 + i,
        block_number=2000 + i,
        tx_hash=f"0xrfq{i:060x}",
        log_index=i,
        pair_id=pair,
        order_hash=f"0xord{i}",
    )


def test_pair_attribution_mechanism_and_convergence() -> None:
    cfg = _cfg()
    bot = "0x" + "d1" * 20
    # 20 converging buys → arb_bot
    amm = [
        _amm(taker=bot, direction="buy_native", fluxion="95", bybit="100", i=i)
        for i in range(20)
    ]
    rfq = [_rfq(pair="AAPLx", i=0), _rfq(pair="AAPLx", i=1)]
    # unscoped RFQ must not count in pair view
    rfq.append(_rfq(pair=None, i=99))

    panel = build_pair_attribution(
        pair_id="AAPLx",
        amm_trades=amm,
        rfq_fills=rfq,
        config=cfg,
        contract_flags={bot: True},
    )
    assert panel.mechanism.amm_trades == 20
    assert panel.mechanism.rfq_trades == 2
    assert panel.mechanism.rfq_share == 2 / 22
    assert panel.convergence_share == 1.0
    assert panel.label_trade_share[BehaviorLabel.ARB_BOT] == 1.0
    assert len(panel.top_takers) == 1
    assert panel.top_takers[0].label is BehaviorLabel.ARB_BOT
    assert panel.top_takers[0].is_contract is True
    assert panel.window_start_ms is not None
    assert panel.window_end_ms is not None


def test_session_filter_open_only() -> None:
    cfg = _cfg()
    taker = "0x" + "ee" * 20
    amm = [
        _amm(taker=taker, session=SessionKind.OPEN, i=0),
        _amm(taker=taker, session=SessionKind.CLOSED, i=1),
        _amm(taker=taker, session=SessionKind.OPEN, i=2),
    ]
    open_panel = build_pair_attribution(
        pair_id="AAPLx", amm_trades=amm, config=cfg, session=SessionKind.OPEN
    )
    assert open_panel.mechanism.amm_trades == 2
    assert open_panel.session is SessionKind.OPEN


def test_global_mechanism_includes_unscoped_rfq() -> None:
    amm = [_amm(i=0)]
    rfq = [_rfq(pair="AAPLx", i=0), _rfq(pair=None, i=1)]
    share = build_global_mechanism_share(amm, rfq)
    assert share.amm_trades == 1
    assert share.rfq_trades == 2


def test_snapshot_multi_pair() -> None:
    cfg = _cfg()
    a = "0x" + "a1" * 20
    b = "0x" + "b2" * 20
    amm = [_amm(pair="AAPLx", taker=a, i=i) for i in range(5)]
    amm += [_amm(pair="TSLAx", taker=b, i=100 + i) for i in range(3)]
    snap = build_attribution_snapshot(
        amm_trades=amm,
        rfq_fills=[_rfq(pair=None, i=0)],
        config=cfg,
    )
    assert set(snap.pairs) == {"AAPLx", "TSLAx"}
    assert snap.global_mechanism.amm_trades == 8
    assert snap.global_mechanism.rfq_trades == 1
    assert snap.pairs["AAPLx"].mechanism.amm_trades == 5
    assert snap.pairs["TSLAx"].mechanism.amm_trades == 3


def test_mechanism_of_trade() -> None:
    assert mechanism_of_trade(_amm()) is Mechanism.AMM
    assert mechanism_of_trade(_rfq()) is Mechanism.RFQ
