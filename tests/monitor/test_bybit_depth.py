"""Seams: multi-level book merge + precomputed VWAP curve (WHI-755)."""

from __future__ import annotations

from decimal import Decimal

from monitor.bybit.depth import DepthBookTracker
from monitor.bybit.depth_math import (
    apply_side_ops,
    book_notional_depth,
    book_vwap_for_base,
    book_vwap_for_notional,
    de_multiplied_levels,
    de_multiplied_size,
    sorted_ask_levels,
    sorted_bid_levels,
    vwap_curve,
)
from monitor.bybit.ws import DepthEmitThrottle
from monitor.quotes import BybitBookTick

PAIR_IDS = {"AAPLXUSDT": "AAPLx", "TSLAXUSDT": "TSLAx"}
MULTIPLIERS = {"AAPLXUSDT": Decimal("2"), "TSLAXUSDT": Decimal("1")}
BUCKETS = [Decimal("10"), Decimal("50"), Decimal("100")]


def _tracker() -> DepthBookTracker:
    return DepthBookTracker(
        pair_id_by_symbol=PAIR_IDS,
        multiplier_by_symbol=MULTIPLIERS,
        buckets_usd=BUCKETS,
    )


def test_de_multiplied_size_preserves_notional() -> None:
    # raw price 200, size 1, m=2 → dm price 100, dm size 2 → notional 200
    assert de_multiplied_size(Decimal("1"), Decimal("2")) == Decimal("2")
    levels = de_multiplied_levels([(Decimal("200"), Decimal("1"))], Decimal("2"))
    assert levels == [(Decimal("100"), Decimal("2"))]
    assert levels[0][0] * levels[0][1] == Decimal("200")


def test_apply_side_ops_snapshot_and_delta() -> None:
    snap = apply_side_ops(
        {},
        [(Decimal("100"), Decimal("1")), (Decimal("99"), Decimal("2"))],
        is_snapshot=True,
    )
    assert sorted_bid_levels(snap)[0] == (Decimal("100"), Decimal("1"))
    nxt = apply_side_ops(
        snap,
        [(Decimal("100"), Decimal("0")), (Decimal("101"), Decimal("0.5"))],
        is_snapshot=False,
    )
    assert Decimal("100") not in nxt
    assert nxt[Decimal("101")] == Decimal("0.5")
    assert nxt[Decimal("99")] == Decimal("2")


def test_book_vwap_hand_example() -> None:
    levels = [(Decimal("100"), Decimal("1")), (Decimal("99"), Decimal("2"))]
    vwap = book_vwap_for_notional(levels, Decimal("150"))
    assert vwap is not None
    expected = Decimal("150") / (Decimal("1") + Decimal("50") / Decimal("99"))
    assert abs(vwap - expected) < Decimal("1e-9")


def test_book_vwap_unfillable() -> None:
    levels = [(Decimal("100"), Decimal("0.1"))]  # $10 depth
    assert book_vwap_for_notional(levels, Decimal("50")) is None


def test_book_vwap_for_base_and_notional_depth() -> None:
    levels = [(Decimal("100"), Decimal("1")), (Decimal("99"), Decimal("2"))]
    vwap = book_vwap_for_base(levels, Decimal("2"))
    assert vwap is not None
    # 1@100 + 1@99 → notional 199 / 2
    assert abs(vwap - Decimal("199") / Decimal("2")) < Decimal("1e-12")
    assert book_notional_depth(levels) == Decimal("100") + Decimal("198")


def test_depth_snapshot_multi_level_vwap() -> None:
    tracker = _tracker()
    payload = {
        "topic": "orderbook.50.TSLAXUSDT",
        "type": "snapshot",
        "ts": 1_700_000_000_000,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100.0", "1.0"], ["99.0", "2.0"], ["98.0", "10.0"]],
            "a": [["101.0", "1.0"], ["102.0", "2.0"], ["103.0", "10.0"]],
            "u": 1,
            "seq": 100,
        },
    }
    book = tracker.apply(payload, recv_ts_ms=1_700_000_000_010)
    assert book is not None
    assert book.bid == Decimal("100.0")
    assert book.ask == Decimal("101.0")
    d = tracker.depth_tick("TSLAXUSDT")
    assert d is not None
    assert d.buckets_usd == tuple(BUCKETS)
    assert d.bid_vwap_dm[0] == Decimal("100")
    assert d.ask_vwap_dm[0] == Decimal("101")
    assert d.bid_vwap_dm[1] == Decimal("100")
    assert d.bid_vwap_dm[2] == Decimal("100")


def test_depth_applies_multiplier_before_vwap() -> None:
    tracker = _tracker()
    payload = {
        "topic": "orderbook.50.AAPLXUSDT",
        "type": "snapshot",
        "ts": 1_700_000_000_000,
        "data": {
            "s": "AAPLXUSDT",
            "b": [["200.0", "1.0"]],
            "a": [["202.0", "1.0"]],
            "u": 1,
            "seq": 1,
        },
    }
    book = tracker.apply(payload)
    assert book is not None
    assert book.bid_de_multiplied == Decimal("100")
    d = tracker.depth_tick("AAPLXUSDT")
    assert d is not None
    assert d.bid_vwap_dm[0] == Decimal("100")
    assert d.bid_vwap_dm[1] == Decimal("100")
    assert d.bid_vwap_dm[2] == Decimal("100")


def test_depth_delta_merge_and_delete() -> None:
    tracker = _tracker()
    snap = {
        "topic": "orderbook.50.TSLAXUSDT",
        "type": "snapshot",
        "ts": 1_700_000_000_000,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100.0", "1"], ["99.0", "1"]],
            "a": [["101.0", "1"], ["102.0", "1"]],
            "u": 1,
            "seq": 1,
        },
    }
    assert tracker.apply(snap) is not None
    delta = {
        "topic": "orderbook.50.TSLAXUSDT",
        "type": "delta",
        "ts": 1_700_000_000_100,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100.0", "0"], ["100.5", "2"]],
            "a": [["101.0", "0.5"]],
            "u": 2,
            "seq": 2,
        },
    }
    book = tracker.apply(delta, gap=True)
    assert book is not None
    assert book.bid == Decimal("100.5")
    assert book.ask == Decimal("101.0")
    assert book.gap is True
    d = tracker.depth_tick("TSLAXUSDT")
    assert d is not None
    assert d.gap is True


def test_orphan_delta_dropped() -> None:
    tracker = _tracker()
    delta = {
        "topic": "orderbook.50.TSLAXUSDT",
        "type": "delta",
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100", "1"]],
            "a": [["101", "1"]],
            "u": 5,
            "seq": 5,
        },
    }
    assert tracker.apply(delta) is None


def test_u_gap_sticky_until_snapshot() -> None:
    tracker = _tracker()
    snap = {
        "topic": "orderbook.50.TSLAXUSDT",
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
        "topic": "orderbook.50.TSLAXUSDT",
        "type": "delta",
        "ts": 2,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100.1", "1"]],
            "a": [["101", "1"]],
            "u": 5,  # skipped 2,3,4
            "seq": 2,
        },
    }
    book = tracker.apply(jump)
    assert book is not None
    assert book.gap is True
    # Further deltas stay gapped until a snapshot re-establishes the full book.
    cont = {
        "topic": "orderbook.50.TSLAXUSDT",
        "type": "delta",
        "ts": 3,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100.2", "1"]],
            "a": [["101", "1"]],
            "u": 6,
            "seq": 3,
        },
    }
    book2 = tracker.apply(cont)
    assert book2 is not None
    assert book2.gap is True
    heal = {
        "topic": "orderbook.50.TSLAXUSDT",
        "type": "snapshot",
        "ts": 4,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100", "2"]],
            "a": [["101", "2"]],
            "u": 10,
            "seq": 10,
        },
    }
    book3 = tracker.apply(heal)
    assert book3 is not None
    assert book3.gap is False


def test_vwap_curve_parallel_buckets() -> None:
    levels = sorted_ask_levels(
        {Decimal("10"): Decimal("1"), Decimal("11"): Decimal("10")}
    )
    curve = vwap_curve(levels, [Decimal("5"), Decimal("50")])
    assert curve[0] == Decimal("10")
    expected = Decimal("50") / (Decimal("1") + Decimal("40") / Decimal("11"))
    assert curve[1] is not None
    assert abs(curve[1] - expected) < Decimal("1e-9")


def _book(
    *,
    pair: str = "TSLAx",
    recv: int,
    bid: str = "100",
    ask: str = "101",
) -> BybitBookTick:
    return BybitBookTick(
        pair_id=pair,
        symbol="TSLAXUSDT",
        exchange_ts_ms=recv,
        recv_ts_ms=recv,
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_de_multiplied=Decimal(bid),
        ask_de_multiplied=Decimal(ask),
        multiplier=Decimal("1"),
    )


def test_depth_emit_throttle_interval() -> None:
    thr = DepthEmitThrottle(emit_interval_ms=1000, mid_change_bps=Decimal("0"))
    b0 = _book(recv=1_000)
    assert thr.should_emit(b0) is True
    thr.mark_emitted(b0)
    assert thr.should_emit(_book(recv=1_500)) is False
    assert thr.should_emit(_book(recv=2_000)) is True


def test_depth_emit_throttle_mid_move() -> None:
    thr = DepthEmitThrottle(emit_interval_ms=10_000, mid_change_bps=Decimal("10"))
    b0 = _book(recv=1_000, bid="100", ask="100")  # mid 100
    thr.mark_emitted(b0)
    # 5 bps move — below threshold
    assert thr.should_emit(_book(recv=1_100, bid="100.05", ask="100.05")) is False
    # 20 bps move
    assert thr.should_emit(_book(recv=1_200, bid="100.20", ask="100.20")) is True


def test_l1_dedupe_includes_gap_flag() -> None:
    """Daemon fingerprint is (bid, ask, gap) — sticky gap must not re-amplify."""
    last: dict[str, tuple[Decimal, Decimal, bool]] = {}

    def should_write(pair: str, bid: Decimal, ask: Decimal, *, gap: bool) -> bool:
        key = (bid, ask, gap)
        if last.get(pair) == key:
            return False
        last[pair] = key
        return True

    assert should_write("TSLAx", Decimal("1"), Decimal("2"), gap=False)
    assert should_write("TSLAx", Decimal("1"), Decimal("2"), gap=False) is False
    # First gapped tick at same L1 still journals once.
    assert should_write("TSLAx", Decimal("1"), Decimal("2"), gap=True) is True
    assert should_write("TSLAx", Decimal("1"), Decimal("2"), gap=True) is False
    assert should_write("TSLAx", Decimal("1"), Decimal("3"), gap=True) is True
