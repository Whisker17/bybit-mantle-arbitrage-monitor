"""Seams: Bybit WS message parse + de-multiplied prices + subscribe args."""

from __future__ import annotations

from decimal import Decimal

from monitor.bybit.parse import (
    build_subscribe_args,
    parse_orderbook_message,
    parse_public_trade_message,
)


def test_parse_orderbook_applies_multiplier() -> None:
    mult = Decimal("2")
    payload = {
        "topic": "orderbook.1.AAPLXUSDT",
        "ts": 1_700_000_000_000,
        "data": {
            "s": "AAPLXUSDT",
            "b": [["200.0", "1"]],
            "a": [["201.0", "2"]],
            "ts": 1_700_000_000_000,
        },
    }
    tick = parse_orderbook_message(
        payload,
        pair_id_by_symbol={"AAPLXUSDT": "AAPLx"},
        multiplier_by_symbol={"AAPLXUSDT": mult},
        recv_ts_ms=1_700_000_000_010,
    )
    assert tick is not None
    assert tick.pair_id == "AAPLx"
    assert tick.bid == Decimal("200.0")
    assert tick.ask == Decimal("201.0")
    assert tick.bid_de_multiplied == Decimal("100.0")
    assert tick.ask_de_multiplied == Decimal("100.5")
    assert tick.gap is False


def test_parse_orderbook_unknown_symbol_returns_none() -> None:
    payload = {
        "topic": "orderbook.1.BTCUSDT",
        "data": {"s": "BTCUSDT", "b": [["1", "1"]], "a": [["2", "1"]]},
    }
    assert (
        parse_orderbook_message(
            payload,
            pair_id_by_symbol={"TSLAXUSDT": "TSLAx"},
            multiplier_by_symbol={"TSLAXUSDT": Decimal("1")},
        )
        is None
    )


def test_parse_public_trades() -> None:
    payload = {
        "topic": "publicTrade.TSLAXUSDT",
        "data": [
            {
                "s": "TSLAXUSDT",
                "p": "250.5",
                "v": "0.1",
                "S": "Buy",
                "i": "tid-1",
                "T": 1_700_000_000_100,
            }
        ],
    }
    ticks = parse_public_trade_message(
        payload,
        pair_id_by_symbol={"TSLAXUSDT": "TSLAx"},
        multiplier_by_symbol={"TSLAXUSDT": Decimal("1")},
        recv_ts_ms=1_700_000_000_110,
        gap=True,
    )
    assert len(ticks) == 1
    assert ticks[0].trade_id == "tid-1"
    assert ticks[0].side == "Buy"
    assert ticks[0].price_de_multiplied == Decimal("250.5")
    assert ticks[0].gap is True


def test_build_subscribe_args() -> None:
    args = build_subscribe_args(["TSLAXUSDT", "AAPLXUSDT"])
    assert "orderbook.1.TSLAXUSDT" in args
    assert "publicTrade.TSLAXUSDT" in args
    assert "orderbook.1.AAPLXUSDT" in args
    assert len(args) == 4
