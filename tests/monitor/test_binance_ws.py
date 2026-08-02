"""Seam: BinanceWsCollector topic dispatch (book / depth / trade)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from monitor.binance.ws import BinanceWsCollector
from monitor.quotes import BybitBookTick, BybitDepthTick, BybitTradeTick, CollectorGap


@pytest.mark.asyncio
async def test_handle_raw_book_ticker_and_depth_and_trade() -> None:
    books: list[BybitBookTick] = []
    depths: list[BybitDepthTick] = []
    trades: list[BybitTradeTick] = []
    gaps: list[CollectorGap] = []

    coll = BinanceWsCollector(
        ws_base_url="wss://data-stream.binance.vision",
        symbols=["TSLABUSDT"],
        pair_id_by_symbol={"TSLABUSDT": "TSLAB"},
        ui_multiplier_by_symbol={"TSLABUSDT": Decimal("1")},
        on_book=books.append,
        on_trade=trades.append,
        on_depth=depths.append,
        on_gap=gaps.append,
        depth_enabled=True,
        depth_buckets_usd=[Decimal("10")],
        depth_emit_interval_ms=1000,
        depth_mid_change_bps=Decimal("0"),
    )

    await coll._handle_raw(
        json.dumps(
            {
                "stream": "tslabusdt@bookTicker",
                "data": {
                    "u": 1,
                    "s": "TSLABUSDT",
                    "b": "250.0",
                    "B": "1",
                    "a": "251.0",
                    "A": "1",
                },
            }
        )
    )
    await coll._handle_raw(
        json.dumps(
            {
                "stream": "tslabusdt@depth20@100ms",
                "data": {
                    "lastUpdateId": 5,
                    "bids": [["250.0", "1"], ["249.0", "2"]],
                    "asks": [["251.0", "1"], ["252.0", "3"]],
                },
            }
        )
    )
    await coll._handle_raw(
        json.dumps(
            {
                "stream": "tslabusdt@aggTrade",
                "data": {
                    "e": "aggTrade",
                    "E": 1000,
                    "s": "TSLABUSDT",
                    "a": 7,
                    "p": "250.5",
                    "q": "0.2",
                    "T": 1001,
                    "m": False,
                },
            }
        )
    )

    assert len(books) == 1
    assert books[0].bid == Decimal("250.0")
    assert len(depths) == 1
    assert depths[0].depth_levels >= 1
    assert len(trades) == 1
    assert trades[0].side == "Buy"
    assert gaps == []


@pytest.mark.asyncio
async def test_depth_seeds_l1_when_book_ticker_absent() -> None:
    books: list[BybitBookTick] = []
    coll = BinanceWsCollector(
        ws_base_url="wss://example",
        symbols=["TSLABUSDT"],
        pair_id_by_symbol={"TSLABUSDT": "TSLAB"},
        ui_multiplier_by_symbol={"TSLABUSDT": Decimal("1")},
        on_book=books.append,
        on_trade=lambda _t: None,
        depth_enabled=False,
    )
    await coll._handle_raw(
        json.dumps(
            {
                "stream": "tslabusdt@depth20@100ms",
                "data": {
                    "lastUpdateId": 1,
                    "bids": [["10", "1"]],
                    "asks": [["11", "1"]],
                },
            }
        )
    )
    # Second depth should not re-seed L1.
    await coll._handle_raw(
        json.dumps(
            {
                "stream": "tslabusdt@depth20@100ms",
                "data": {
                    "lastUpdateId": 2,
                    "bids": [["10.5", "1"]],
                    "asks": [["11", "1"]],
                },
            }
        )
    )
    assert len(books) == 1
    assert books[0].bid == Decimal("10")
