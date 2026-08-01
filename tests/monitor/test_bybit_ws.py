"""Seam: BybitWsCollector topic dispatch + shared DepthBookTracker wiring."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from monitor.bybit.ws import BybitWsCollector
from monitor.quotes import BybitBookTick, BybitDepthTick, BybitTradeTick


@pytest.mark.asyncio
async def test_handle_raw_orderbook_uses_shared_depth_tracker() -> None:
    books: list[BybitBookTick] = []
    depths: list[BybitDepthTick] = []
    trades: list[BybitTradeTick] = []

    coll = BybitWsCollector(
        ws_url="wss://example",
        symbols=["TSLAXUSDT"],
        pair_id_by_symbol={"TSLAXUSDT": "TSLAx"},
        multiplier_by_symbol={"TSLAXUSDT": Decimal("1")},
        on_book=books.append,
        on_trade=trades.append,
        on_depth=depths.append,
        book_topic_prefix="orderbook.50",
        depth_enabled=True,
        depth_buckets_usd=[Decimal("10"), Decimal("50")],
    )

    snap = {
        "topic": "orderbook.50.TSLAXUSDT",
        "type": "snapshot",
        "ts": 1000,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100", "1"], ["99", "2"]],
            "a": [["101", "1"], ["102", "2"]],
            "u": 1,
            "seq": 1,
        },
    }
    delta = {
        "topic": "orderbook.50.TSLAXUSDT",
        "type": "delta",
        "ts": 1001,
        "data": {
            "s": "TSLAXUSDT",
            "b": [],
            "a": [["102", "1"]],
            "u": 2,
            "seq": 2,
        },
    }
    await coll._handle_raw(json.dumps(snap))
    await coll._handle_raw(json.dumps(delta))

    assert len(books) == 2
    assert books[0].bid == Decimal("100")
    assert books[0].ask == Decimal("101")
    assert books[1].bid == Decimal("100")  # retained across shared tracker
    assert books[1].ask == Decimal("101")  # best ask unchanged; size on 102 updated
    assert len(depths) == 2
    assert depths[0].bid_vwap_dm[0] == Decimal("100")
    assert trades == []


@pytest.mark.asyncio
async def test_handle_raw_ignores_tickers_when_prefix_is_orderbook() -> None:
    books: list[BybitBookTick] = []
    coll = BybitWsCollector(
        ws_url="wss://example",
        symbols=["TSLAXUSDT"],
        pair_id_by_symbol={"TSLAXUSDT": "TSLAx"},
        multiplier_by_symbol={"TSLAXUSDT": Decimal("1")},
        on_book=books.append,
        on_trade=lambda _t: None,
        book_topic_prefix="orderbook.1",
    )
    await coll._handle_raw(
        json.dumps(
            {
                "topic": "tickers.TSLAXUSDT",
                "data": {
                    "symbol": "TSLAXUSDT",
                    "bid1Price": "1",
                    "ask1Price": "2",
                },
            }
        )
    )
    assert books == []
