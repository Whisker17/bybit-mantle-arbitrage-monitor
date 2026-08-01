"""Seam: BybitWsCollector topic dispatch + shared L1BookTracker wiring."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from monitor.bybit.ws import BybitWsCollector
from monitor.quotes import BybitBookTick, BybitTradeTick


@pytest.mark.asyncio
async def test_handle_raw_orderbook_uses_shared_l1_tracker() -> None:
    books: list[BybitBookTick] = []
    trades: list[BybitTradeTick] = []

    coll = BybitWsCollector(
        ws_url="wss://example",
        symbols=["TSLAXUSDT"],
        pair_id_by_symbol={"TSLAXUSDT": "TSLAx"},
        multiplier_by_symbol={"TSLAXUSDT": Decimal("1")},
        on_book=books.append,
        on_trade=trades.append,
        book_topic_prefix="orderbook.1",
    )

    snap = {
        "topic": "orderbook.1.TSLAXUSDT",
        "type": "snapshot",
        "ts": 1000,
        "data": {
            "s": "TSLAXUSDT",
            "b": [["100", "1"]],
            "a": [["101", "1"]],
            "u": 1,
            "seq": 1,
        },
    }
    delta = {
        "topic": "orderbook.1.TSLAXUSDT",
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
    assert books[1].ask == Decimal("102")
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
