"""Seams: Binance WS parse + multiply-semantics L1/trades (WHI-772)."""

from __future__ import annotations

from decimal import Decimal

from monitor.binance.depth import BinanceDepthTracker, multiplied_levels
from monitor.binance.parse import (
    build_combined_stream_path,
    build_stream_names,
    parse_agg_trade_message,
    parse_book_ticker,
    parse_partial_depth,
    stream_kind,
)
from monitor.binance.ws import join_ws_url
from monitor.symbols.multipliers import multiplied_price, multiplied_size

PAIR_IDS = {"TSLABUSDT": "TSLAB", "MUBUSDT": "MUB"}
MULTS = {"TSLABUSDT": Decimal("1"), "MUBUSDT": Decimal("1.0001075125688057")}


def test_multiplied_price_and_size_preserve_notional() -> None:
    price = Decimal("100")
    size = Decimal("2")
    mult = Decimal("1.5")
    p_dm = multiplied_price(price, mult)
    s_dm = multiplied_size(size, mult)
    assert p_dm == Decimal("150")
    assert s_dm == Decimal("2") / Decimal("1.5")
    assert p_dm * s_dm == price * size


def test_parse_book_ticker_applies_ui_multiplier() -> None:
    payload = {
        "stream": "mubusdt@bookTicker",
        "data": {
            "u": 400_900_217,  # sequence id — must NOT become exchange_ts_ms
            "s": "MUBUSDT",
            "b": "100.0",
            "B": "1",
            "a": "100.2",
            "A": "2",
        },
    }
    tick = parse_book_ticker(
        payload,
        pair_id_by_symbol=PAIR_IDS,
        ui_multiplier_by_symbol=MULTS,
        recv_ts_ms=1_700_000_000_010,
    )
    assert tick is not None
    assert tick.pair_id == "MUB"
    assert tick.bid == Decimal("100.0")
    assert tick.ask == Decimal("100.2")
    assert tick.bid_de_multiplied == multiplied_price(
        Decimal("100.0"), MULTS["MUBUSDT"]
    )
    # Spot bookTicker has no E/T — use recv, never the sequence id `u`.
    assert tick.exchange_ts_ms == 1_700_000_000_010
    assert tick.gap is False


def test_parse_partial_depth_and_tracker_vwap() -> None:
    envelope = {
        "stream": "tslabusdt@depth20@100ms",
        "data": {
            "lastUpdateId": 10,
            "bids": [["250.0", "1"], ["249.0", "2"]],
            "asks": [["251.0", "1"], ["252.0", "2"]],
        },
    }
    parsed = parse_partial_depth(envelope, stream=envelope["stream"])
    assert parsed is not None
    symbol, bids, asks, last_id = parsed
    assert symbol == "TSLABUSDT"
    assert last_id == 10
    assert bids[0][0] == Decimal("250.0")

    tracker = BinanceDepthTracker(
        pair_id_by_symbol=PAIR_IDS,
        ui_multiplier_by_symbol=MULTS,
        buckets_usd=[Decimal("10"), Decimal("50")],
    )
    book = tracker.apply_depth(envelope, recv_ts_ms=100)
    assert book is not None
    assert book.bid == Decimal("250.0")
    depth = tracker.depth_tick("TSLABUSDT")
    assert depth is not None
    assert depth.bid_vwap_dm[0] == Decimal("250.0")
    # Multiply path: levels at mult=1 equal raw.
    lv = multiplied_levels([(Decimal("10"), Decimal("1"))], Decimal("2"))
    assert lv[0] == (Decimal("20"), Decimal("0.5"))


def test_parse_agg_trade_buyer_maker_is_sell() -> None:
    payload = {
        "stream": "tslabusdt@aggTrade",
        "data": {
            "e": "aggTrade",
            "E": 1_700_000_000_000,
            "s": "TSLABUSDT",
            "a": 99,
            "p": "250.5",
            "q": "0.1",
            "T": 1_700_000_000_001,
            "m": True,
        },
    }
    trades = parse_agg_trade_message(
        payload,
        pair_id_by_symbol=PAIR_IDS,
        ui_multiplier_by_symbol=MULTS,
        recv_ts_ms=1_700_000_000_010,
    )
    assert len(trades) == 1
    assert trades[0].side == "Sell"
    assert trades[0].price == Decimal("250.5")
    assert trades[0].trade_id == "99"


def test_stream_names_and_url() -> None:
    names = build_stream_names(["TSLABUSDT"], depth_enabled=True)
    assert "tslabusdt@bookTicker" in names
    assert "tslabusdt@depth20@100ms" in names
    assert "tslabusdt@aggTrade" in names
    path = build_combined_stream_path(["TSLABUSDT"])
    assert path.startswith("/stream?streams=")
    url = join_ws_url("wss://data-stream.binance.vision", path)
    assert url.startswith("wss://data-stream.binance.vision/stream?streams=")
    assert stream_kind("tslabusdt@bookTicker") == "book"
    assert stream_kind("tslabusdt@depth20@100ms") == "depth"
    assert stream_kind("tslabusdt@aggTrade") == "trade"
