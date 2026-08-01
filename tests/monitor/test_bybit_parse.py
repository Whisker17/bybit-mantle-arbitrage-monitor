"""Seams: Bybit WS message parse + de-multiplied prices + subscribe args.

Spot public WS has no bid1/ask1 on tickers; L1 comes from orderbook.1 (WHI-743).
"""

from __future__ import annotations

from decimal import Decimal

from monitor.bybit.parse import (
    build_subscribe_args,
    parse_public_trade_message,
    parse_ticker_message,
)


def test_parse_orderbook_snapshot_applies_multiplier() -> None:
    """orderbook.1 snapshot: data.b/a are [[price, size], ...]; apply multiplier."""
    mult = Decimal("2")
    payload = {
        "topic": "orderbook.1.AAPLXUSDT",
        "type": "snapshot",
        "ts": 1_700_000_000_000,
        "data": {
            "s": "AAPLXUSDT",
            "b": [["200.0", "1.0"]],
            "a": [["201.0", "2.0"]],
            "u": 1,
            "seq": 100,
        },
    }
    tick = parse_ticker_message(
        payload,
        pair_id_by_symbol={"AAPLXUSDT": "AAPLx"},
        multiplier_by_symbol={"AAPLXUSDT": mult},
        recv_ts_ms=1_700_000_000_010,
    )
    assert tick is not None
    assert tick.pair_id == "AAPLx"
    assert tick.symbol == "AAPLXUSDT"
    assert tick.bid == Decimal("200.0")
    assert tick.ask == Decimal("201.0")
    assert tick.bid_de_multiplied == Decimal("100.0")
    assert tick.ask_de_multiplied == Decimal("100.5")
    assert tick.exchange_ts_ms == 1_700_000_000_000
    assert tick.gap is False


def test_parse_orderbook_delta_l1_full_level() -> None:
    """At depth=1, delta carries the full top-of-book level (not a partial patch)."""
    payload = {
        "topic": "orderbook.1.TSLAXUSDT",
        "type": "delta",
        "ts": 1_700_000_000_200,
        "cts": 1_700_000_000_199,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["250.5", "0.5"]],
            "a": [["251.0", "0.25"]],
            "u": 2,
            "seq": 101,
        },
    }
    tick = parse_ticker_message(
        payload,
        pair_id_by_symbol={"TSLAXUSDT": "TSLAx"},
        multiplier_by_symbol={"TSLAXUSDT": Decimal("1")},
        recv_ts_ms=1_700_000_000_210,
        gap=True,
    )
    assert tick is not None
    assert tick.bid == Decimal("250.5")
    assert tick.ask == Decimal("251.0")
    assert tick.gap is True


def test_parse_orderbook_empty_levels_returns_none() -> None:
    """Empty bid or ask ladder → drop (cannot form L1)."""
    base = {
        "topic": "orderbook.1.AAPLXUSDT",
        "type": "delta",
        "ts": 1_700_000_000_000,
        "data": {
            "s": "AAPLXUSDT",
            "b": [["200.0", "1.0"]],
            "a": [["201.0", "1.0"]],
            "u": 1,
            "seq": 1,
        },
    }
    maps = {
        "pair_id_by_symbol": {"AAPLXUSDT": "AAPLx"},
        "multiplier_by_symbol": {"AAPLXUSDT": Decimal("1")},
    }
    empty_bid = {**base, "data": {**base["data"], "b": []}}
    empty_ask = {**base, "data": {**base["data"], "a": []}}
    zero_size = {
        **base,
        "data": {**base["data"], "b": [["200.0", "0"]], "a": [["201.0", "1.0"]]},
    }
    assert parse_ticker_message(empty_bid, **maps) is None
    assert parse_ticker_message(empty_ask, **maps) is None
    assert parse_ticker_message(zero_size, **maps) is None


def test_parse_orderbook_symbol_from_topic_when_data_s_missing() -> None:
    payload = {
        "topic": "orderbook.1.AAPLXUSDT",
        "type": "snapshot",
        "ts": 1_700_000_000_000,
        "data": {
            "b": [["10", "1"]],
            "a": [["11", "1"]],
            "u": 1,
            "seq": 1,
        },
    }
    tick = parse_ticker_message(
        payload,
        pair_id_by_symbol={"AAPLXUSDT": "AAPLx"},
        multiplier_by_symbol={"AAPLXUSDT": Decimal("1")},
    )
    assert tick is not None
    assert tick.pair_id == "AAPLx"


def test_parse_orderbook_unknown_symbol_returns_none() -> None:
    payload = {
        "topic": "orderbook.1.BTCUSDT",
        "type": "snapshot",
        "data": {
            "s": "BTCUSDT",
            "b": [["1", "1"]],
            "a": [["2", "1"]],
        },
    }
    assert (
        parse_ticker_message(
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


def test_build_subscribe_args_uses_orderbook_l1() -> None:
    args = build_subscribe_args(["TSLAXUSDT", "AAPLXUSDT"])
    assert "orderbook.1.TSLAXUSDT" in args
    assert "publicTrade.TSLAXUSDT" in args
    assert "orderbook.1.AAPLXUSDT" in args
    assert "publicTrade.AAPLXUSDT" in args
    assert len(args) == 4
    # Spot tickers must not be used for L1 (no bid1/ask1 on spot public stream).
    assert not any(a.startswith("tickers.") for a in args)
