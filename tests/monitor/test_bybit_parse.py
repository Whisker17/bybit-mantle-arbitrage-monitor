"""Seams: Bybit WS message parse + L1 merge + subscribe args.

Spot public WS has no bid1/ask1 on tickers; L1 comes from orderbook.1 (WHI-743).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from monitor.bybit.l1 import L1BookTracker
from monitor.bybit.parse import (
    apply_l1_side,
    build_subscribe_args,
    parse_orderbook_l1_update,
    parse_public_trade_message,
)

PAIR_IDS = {"AAPLXUSDT": "AAPLx", "TSLAXUSDT": "TSLAx"}
MULTIPLIERS = {"AAPLXUSDT": Decimal("2"), "TSLAXUSDT": Decimal("1")}


def _tracker() -> L1BookTracker:
    return L1BookTracker(pair_id_by_symbol=PAIR_IDS, multiplier_by_symbol=MULTIPLIERS)


def test_parse_orderbook_snapshot_applies_multiplier() -> None:
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
    tick = _tracker().apply(payload, recv_ts_ms=1_700_000_000_010)
    assert tick is not None
    assert tick.pair_id == "AAPLx"
    assert tick.symbol == "AAPLXUSDT"
    assert tick.bid == Decimal("200.0")
    assert tick.ask == Decimal("201.0")
    assert tick.bid_de_multiplied == Decimal("100.0")
    assert tick.ask_de_multiplied == Decimal("100.5")
    assert tick.exchange_ts_ms == 1_700_000_000_000
    assert tick.gap is False


def test_parse_orderbook_delta_after_snapshot() -> None:
    tracker = _tracker()
    snap = {
        "topic": "orderbook.1.TSLAXUSDT",
        "type": "snapshot",
        "ts": 1_700_000_000_000,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["250.0", "1"]],
            "a": [["251.0", "1"]],
            "u": 1,
            "seq": 100,
        },
    }
    assert tracker.apply(snap) is not None
    delta = {
        "topic": "orderbook.1.TSLAXUSDT",
        "type": "delta",
        "ts": 1_700_000_000_200,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["250.5", "0.5"]],
            "a": [["251.0", "0.25"]],
            "u": 2,
            "seq": 101,
        },
    }
    tick = tracker.apply(delta, recv_ts_ms=1_700_000_000_210, gap=True)
    assert tick is not None
    assert tick.bid == Decimal("250.5")
    assert tick.ask == Decimal("251.0")
    assert tick.gap is True


def test_l1_tracker_merges_one_sided_delta() -> None:
    tracker = _tracker()
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


def test_l1_multi_entry_delta_delete_then_insert() -> None:
    """Price move as [[old,0],[new,sz]] must land on the new level, not clear."""
    tracker = _tracker()
    snap = {
        "type": "snapshot",
        "ts": 1,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["200", "1"]],
            "a": [["201", "1"]],
            "u": 1,
            "seq": 1,
        },
    }
    assert tracker.apply(snap) is not None
    delta = {
        "type": "delta",
        "ts": 2,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["200", "0"], ["199", "2"]],
            "a": [],
            "u": 2,
            "seq": 2,
        },
    }
    tick = tracker.apply(delta)
    assert tick is not None
    assert tick.bid == Decimal("199")
    assert tick.ask == Decimal("201")


def test_apply_l1_side_snapshot_picks_best_bid() -> None:
    ops = [(Decimal("10"), Decimal("1")), (Decimal("12"), Decimal("1"))]
    assert apply_l1_side(None, ops, is_snapshot=True, prefer_high=True) == Decimal("12")
    assert apply_l1_side(None, ops, is_snapshot=True, prefer_high=False) == Decimal("10")


def test_apply_l1_side_delta_delete_clears_to_none() -> None:
    """WHI-971: size-0 delete of the current L1 must yield None, not keep Decimal.

    Pins the intentional Optional assignment that strict mypy flagged as a
    None-leak — clearing L1 is correct when the book side has no level left.
    """
    current = Decimal("200")
    cleared = apply_l1_side(
        current,
        [(Decimal("200"), Decimal(0))],
        is_snapshot=False,
        prefer_high=True,
    )
    assert cleared is None
    # Delete of a different price leaves the current L1 alone.
    kept = apply_l1_side(
        current,
        [(Decimal("199"), Decimal(0))],
        is_snapshot=False,
        prefer_high=True,
    )
    assert kept == current


def test_optional_int_narrows_without_stringifying_floats() -> None:
    """WHI-971: int(float) truncates; int(\"5.0\") must not become None."""
    from monitor.bybit.parse import _optional_int

    assert _optional_int(5) == 5
    assert _optional_int(5.9) == 5
    assert _optional_int("42") == 42
    assert _optional_int(True) == 1
    assert _optional_int(None) is None
    assert _optional_int("") is None
    assert _optional_int("nope") is None


def test_l1_tracker_drops_stale_u() -> None:
    tracker = _tracker()
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


def test_l1_tracker_ignores_delta_before_snapshot() -> None:
    tracker = _tracker()
    orphan = {
        "type": "delta",
        "ts": 1,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100", "1"]],
            "a": [["101", "1"]],
            "u": 1,
            "seq": 1,
        },
    }
    assert tracker.apply(orphan) is None


def test_l1_tracker_drops_crossed_book() -> None:
    tracker = _tracker()
    snap = {
        "type": "snapshot",
        "ts": 1,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100", "1"]],
            "a": [["101", "1"]],
            "u": 1,
            "seq": 1,
        },
    }
    assert tracker.apply(snap) is not None
    # One-sided delta that would cross the retained bid.
    crossed = {
        "type": "delta",
        "ts": 2,
        "data": {
            "s": "TSLAXUSDT",
            "b": [],
            "a": [["99", "1"]],
            "u": 2,
            "seq": 2,
        },
    }
    assert tracker.apply(crossed) is None


def test_l1_tracker_marks_gap_on_noncontiguous_u() -> None:
    tracker = _tracker()
    snap = {
        "type": "snapshot",
        "ts": 1,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100", "1"]],
            "a": [["101", "1"]],
            "u": 1,
            "seq": 1,
        },
    }
    assert tracker.apply(snap) is not None
    jump = {
        "type": "delta",
        "ts": 2,
        "data": {
            "s": "TSLAXUSDT",
            "b": [],
            "a": [["102", "1"]],
            "u": 5,  # skipped 2,3,4
            "seq": 2,
        },
    }
    tick = tracker.apply(jump)
    assert tick is not None
    assert tick.gap is True
    # Subsequent contiguous update is clean.
    next_delta = {
        "type": "delta",
        "ts": 3,
        "data": {
            "s": "TSLAXUSDT",
            "b": [],
            "a": [["103", "1"]],
            "u": 6,
            "seq": 3,
        },
    }
    tick2 = tracker.apply(next_delta)
    assert tick2 is not None
    assert tick2.gap is False


def test_l1_snapshot_without_u_clears_watermark() -> None:
    """Resync snapshot omitting u must not leave a stale high watermark."""
    tracker = _tracker()
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
            # no u / seq
        },
    }
    assert tracker.apply(resync) is not None
    # Low-u delta after watermark clear must be accepted.
    delta = {
        "type": "delta",
        "ts": 3,
        "data": {
            "s": "TSLAXUSDT",
            "b": [],
            "a": [["92", "1"]],
            "u": 1,
            "seq": 1,
        },
    }
    tick = tracker.apply(delta)
    assert tick is not None
    assert tick.ask == Decimal("92")


def test_l1_tracker_snapshot_resets_even_if_u_regresses() -> None:
    tracker = _tracker()
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
    assert _tracker().apply(payload) is None


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
    tick = _tracker().apply(payload)
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
    assert _tracker().apply(payload) is None


def test_parse_orderbook_l1_update_pure() -> None:
    payload = {
        "type": "delta",
        "ts": 9,
        "data": {
            "s": "AAPLXUSDT",
            "b": [["1", "0"], ["2", "3"]],
            "a": [],
            "u": 7,
            "seq": 8,
        },
    }
    upd = parse_orderbook_l1_update(payload)
    assert upd is not None
    assert upd.symbol == "AAPLXUSDT"
    assert upd.bid_ops == [(Decimal("1"), Decimal("0")), (Decimal("2"), Decimal("3"))]
    assert upd.ask_ops == []
    assert upd.u == 7
    assert upd.seq == 8


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


def test_build_subscribe_args_uses_book_prefix() -> None:
    args = build_subscribe_args(["TSLAXUSDT", "AAPLXUSDT"])
    # Default prefix is orderbook.50 (WHI-755); override still works for L1.
    assert "orderbook.50.TSLAXUSDT" in args
    assert "publicTrade.TSLAXUSDT" in args
    assert "orderbook.50.AAPLXUSDT" in args
    assert "publicTrade.AAPLXUSDT" in args
    assert len(args) == 4
    assert not any(a.startswith("tickers.") for a in args)
    l1 = build_subscribe_args(
        ["TSLAXUSDT"], book_prefix="orderbook.1", trade_prefix="publicTrade"
    )
    assert l1 == ["orderbook.1.TSLAXUSDT", "publicTrade.TSLAXUSDT"]
