"""Seams: multi-level book merge + precomputed VWAP curve (WHI-755)."""

from __future__ import annotations

from decimal import Decimal

from monitor.bybit.depth import DepthBookTracker
from monitor.bybit.depth_math import (
    apply_side_ops,
    book_vwap_for_notional,
    de_multiplied_levels,
    de_multiplied_size,
    sorted_ask_levels,
    sorted_bid_levels,
    vwap_curve,
)

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
    # delete top, insert new
    nxt = apply_side_ops(
        snap,
        [(Decimal("100"), Decimal("0")), (Decimal("101"), Decimal("0.5"))],
        is_snapshot=False,
    )
    assert Decimal("100") not in nxt
    assert nxt[Decimal("101")] == Decimal("0.5")
    assert nxt[Decimal("99")] == Decimal("2")


def test_book_vwap_hand_example() -> None:
    # bids: 100×1 + 99×2  → for $150 notional: 100*1 + 99*(50/99) wait:
    # notional walk: take 100 of first level (qty=1), remain 50 → 50/99 qty at 99
    levels = [(Decimal("100"), Decimal("1")), (Decimal("99"), Decimal("2"))]
    vwap = book_vwap_for_notional(levels, Decimal("150"))
    assert vwap is not None
    # spent 150, qty = 1 + 50/99
    expected = Decimal("150") / (Decimal("1") + Decimal("50") / Decimal("99"))
    assert abs(vwap - expected) < Decimal("1e-9")


def test_book_vwap_unfillable() -> None:
    levels = [(Decimal("100"), Decimal("0.1"))]  # $10 depth
    assert book_vwap_for_notional(levels, Decimal("50")) is None


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
    result = tracker.apply(payload, recv_ts_ms=1_700_000_000_010)
    assert result is not None
    assert result.book.bid == Decimal("100.0")
    assert result.book.ask == Decimal("101.0")
    assert result.depth is not None
    d = result.depth
    assert d.buckets_usd == tuple(BUCKETS)
    # $10 fits L1 bid @ 100
    assert d.bid_vwap_dm[0] == Decimal("100")
    assert d.ask_vwap_dm[0] == Decimal("101")
    # $50 still L1 (1*100=100 depth)
    assert d.bid_vwap_dm[1] == Decimal("100")
    # $100 needs second level on bid: 100@100 + 0 notional? 100 notional exact L1
    assert d.bid_vwap_dm[2] == Decimal("100")


def test_depth_applies_multiplier_before_vwap() -> None:
    tracker = _tracker()
    # m=2: raw bid 200 size 1 → dm 100, size 2 → $200 depth at 100
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
    result = tracker.apply(payload)
    assert result is not None
    assert result.book.bid_de_multiplied == Decimal("100")
    assert result.depth is not None
    # $10 and $50 fillable at dm price 100; $100 fillable (depth $200)
    assert result.depth.bid_vwap_dm[0] == Decimal("100")
    assert result.depth.bid_vwap_dm[1] == Decimal("100")
    assert result.depth.bid_vwap_dm[2] == Decimal("100")


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
    result = tracker.apply(delta, gap=True)
    assert result is not None
    assert result.book.bid == Decimal("100.5")
    assert result.book.ask == Decimal("101.0")
    assert result.book.gap is True
    assert result.depth is not None
    assert result.depth.gap is True


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


def test_u_gap_marks_pending_gap_on_next_emit() -> None:
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
    result = tracker.apply(jump)
    assert result is not None
    assert result.book.gap is True


def test_vwap_curve_parallel_buckets() -> None:
    levels = sorted_ask_levels(
        {Decimal("10"): Decimal("1"), Decimal("11"): Decimal("10")}
    )
    curve = vwap_curve(levels, [Decimal("5"), Decimal("50")])
    assert curve[0] == Decimal("10")
    # 10 notional at 10 + 40 at 11
    expected = Decimal("50") / (Decimal("1") + Decimal("40") / Decimal("11"))
    assert curve[1] is not None
    assert abs(curve[1] - expected) < Decimal("1e-9")
