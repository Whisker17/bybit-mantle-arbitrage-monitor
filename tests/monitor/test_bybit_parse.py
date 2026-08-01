"""Seams: Bybit WS message parse + de-multiplied prices + subscribe args.

Spot public WS has no bid1/ask1 on tickers; L1 comes from orderbook.1 (WHI-743).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from monitor.bybit.parse import (
    L1BookTracker,
    build_subscribe_args,
    parse_public_trade_message,
    parse_ticker_message,
)

PAIR_IDS = {"AAPLXUSDT": "AAPLx", "TSLAXUSDT": "TSLAx"}
MULTIPLIERS = {"AAPLXUSDT": Decimal("2"), "TSLAXUSDT": Decimal("1")}


def test_parse_orderbook_snapshot_applies_multiplier() -> None:
    """orderbook.1 snapshot: data.b/a are [[price, size], ...]; apply multiplier."""
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
        multiplier_by_symbol={"AAPLXUSDT": Decimal("2")},
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
    """At depth=1, a complete delta can carry both top-of-book levels."""
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


def test_l1_tracker_merges_one_sided_delta() -> None:
    """Delta with only ask updated keeps prior bid (Bybit empty side = no change)."""
    tracker = L1BookTracker(
        pair_id_by_symbol=PAIR_IDS,
        multiplier_by_symbol=MULTIPLIERS,
    )
    snap = {
        "topic": "orderbook.1.AAPLXUSDT",
        "type": "snapshot",
        "ts": 1_700_000_000_000,
        "data": {
            "s": "AAPLXUSDT",
            "b": [["200.0", "1.0"]],
            "a": [["201.0", "1.0"]],
            "u": 10,
            "seq": 1,
        },
    }
    assert tracker.apply(snap) is not None
    delta = {
        "topic": "orderbook.1.AAPLXUSDT",
        "type": "delta",
        "ts": 1_700_000_000_100,
        "data": {
            "s": "AAPLXUSDT",
            "b": [],
            "a": [["202.0", "3.0"]],
            "u": 11,
            "seq": 2,
        },
    }
    tick = tracker.apply(delta)
    assert tick is not None
    assert tick.bid == Decimal("200.0")
    assert tick.ask == Decimal("202.0")


def test_l1_tracker_drops_stale_u() -> None:
    tracker = L1BookTracker(
        pair_id_by_symbol=PAIR_IDS,
        multiplier_by_symbol=MULTIPLIERS,
    )
    snap = {
        "type": "snapshot",
        "ts": 1,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100", "1"]],
            "a": [["101", "1"]],
            "u": 5,
            "seq": 1,
        },
    }
    assert tracker.apply(snap) is not None
    stale = {
        "type": "delta",
        "ts": 2,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["99", "1"]],
            "a": [["101", "1"]],
            "u": 4,
            "seq": 2,
        },
    }
    assert tracker.apply(stale) is None
    # Prior book unchanged: next good delta still sees last bid 100.
    good = {
        "type": "delta",
        "ts": 3,
        "data": {
            "s": "TSLAXUSDT",
            "b": [],
            "a": [["102", "1"]],
            "u": 6,
            "seq": 3,
        },
    }
    tick = tracker.apply(good)
    assert tick is not None
    assert tick.bid == Decimal("100")
    assert tick.ask == Decimal("102")


def test_l1_tracker_snapshot_resets_even_if_u_regresses() -> None:
    tracker = L1BookTracker(
        pair_id_by_symbol=PAIR_IDS,
        multiplier_by_symbol=MULTIPLIERS,
    )
    first = {
        "type": "snapshot",
        "ts": 1,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100", "1"]],
            "a": [["101", "1"]],
            "u": 50,
            "seq": 10,
        },
    }
    assert tracker.apply(first) is not None
    resync = {
        "type": "snapshot",
        "ts": 2,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["90", "1"]],
            "a": [["91", "1"]],
            "u": 1,
            "seq": 1,
        },
    }
    tick = tracker.apply(resync)
    assert tick is not None
    assert tick.bid == Decimal("90")
    assert tick.ask == Decimal("91")


@pytest.mark.parametrize(
    ("bid", "ask"),
    [
        pytest.param([], [["201.0", "1.0"]], id="empty_bid"),
        pytest.param([["200.0", "1.0"]], [], id="empty_ask"),
        pytest.param([["200.0", "0"]], [["201.0", "1.0"]], id="zero_size_bid"),
        pytest.param([["200.0", "1.0"]], [["201.0", "0"]], id="zero_size_ask"),
    ],
)
def test_parse_orderbook_snapshot_incomplete_l1_returns_none(
    bid: list[list[str]],
    ask: list[list[str]],
) -> None:
    """Snapshot missing a live top-of-book side cannot emit a tick."""
    payload = {
        "topic": "orderbook.1.AAPLXUSDT",
        "type": "snapshot",
        "ts": 1_700_000_000_000,
        "data": {
            "s": "AAPLXUSDT",
            "b": bid,
            "a": ask,
            "u": 1,
            "seq": 1,
        },
    }
    assert (
        parse_ticker_message(
            payload,
            pair_id_by_symbol={"AAPLXUSDT": "AAPLx"},
            multiplier_by_symbol={"AAPLXUSDT": Decimal("1")},
        )
        is None
    )


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
