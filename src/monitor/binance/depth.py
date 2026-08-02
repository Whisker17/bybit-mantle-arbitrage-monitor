"""Partial depth20 snapshots → L1 book + precomputed VWAP curve (WHI-772 / WHI-755).

Binance ``depth20@100ms`` is a full top-N snapshot each push (not Bybit-style
deltas). Multiplier semantics: **multiply** (bStocks BEP-677).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from monitor.binance.parse import parse_book_ticker, parse_partial_depth
from monitor.bybit.depth import DEFAULT_DEPTH_BUCKETS_USD
from monitor.bybit.depth_math import (
    SideMap,
    best_ask,
    best_bid,
    sorted_ask_levels,
    sorted_bid_levels,
    vwap_curve,
)
from monitor.quotes import BybitBookTick, BybitDepthTick, now_ms
from monitor.symbols.multipliers import multiplied_price, multiplied_size


def multiplied_levels(
    levels: list[tuple[Decimal, Decimal]],
    ui_multiplier: Decimal,
) -> list[tuple[Decimal, Decimal]]:
    """Apply price*m and size/m so notional is invariant (multiply semantics)."""
    if ui_multiplier <= 0:
        raise ValueError(f"ui_multiplier must be > 0, got {ui_multiplier}")
    out: list[tuple[Decimal, Decimal]] = []
    for price, size in levels:
        if price <= 0 or size <= 0:
            continue
        out.append(
            (multiplied_price(price, ui_multiplier), multiplied_size(size, ui_multiplier))
        )
    return out


class BinanceDepthTracker:
    """Per-symbol top-N book from partial depth snapshots + optional bookTicker L1."""

    def __init__(
        self,
        *,
        pair_id_by_symbol: Mapping[str, str],
        ui_multiplier_by_symbol: Mapping[str, Decimal],
        buckets_usd: Sequence[Decimal] | None = None,
    ) -> None:
        self._pair_id_by_symbol = dict(pair_id_by_symbol)
        self._ui_multiplier_by_symbol = {
            k.upper(): v for k, v in ui_multiplier_by_symbol.items()
        }
        raw = list(buckets_usd) if buckets_usd is not None else list(DEFAULT_DEPTH_BUCKETS_USD)
        if not raw:
            raise ValueError("buckets_usd must be non-empty when depth is used")
        for q in raw:
            if q <= 0:
                raise ValueError(f"bucket must be > 0, got {q}")
        self._buckets_usd: tuple[Decimal, ...] = tuple(raw)
        self._bids: dict[str, SideMap] = {}
        self._asks: dict[str, SideMap] = {}
        self._last_book: dict[str, BybitBookTick] = {}
        self._last_update_id: dict[str, int] = {}

    @property
    def buckets_usd(self) -> tuple[Decimal, ...]:
        return self._buckets_usd

    def apply_book_ticker(
        self,
        payload: dict[str, Any],
        *,
        recv_ts_ms: int | None = None,
        gap: bool = False,
        stream: str = "",
    ) -> BybitBookTick | None:
        tick = parse_book_ticker(
            payload,
            pair_id_by_symbol=self._pair_id_by_symbol,
            ui_multiplier_by_symbol=self._ui_multiplier_by_symbol,
            recv_ts_ms=recv_ts_ms,
            gap=gap,
            stream=stream,
        )
        if tick is None:
            return None
        self._last_book[tick.symbol] = tick
        return tick

    def apply_depth(
        self,
        payload: dict[str, Any],
        *,
        recv_ts_ms: int | None = None,
        gap: bool = False,
        stream: str = "",
    ) -> BybitBookTick | None:
        parsed = parse_partial_depth(payload, stream=stream)
        if parsed is None:
            return None
        symbol, bids, asks, last_id = parsed
        pair_id = self._pair_id_by_symbol.get(symbol)
        mult = self._ui_multiplier_by_symbol.get(symbol)
        if pair_id is None or mult is None:
            return None

        # Stale snapshot (lastUpdateId not advancing) — drop.
        if last_id is not None:
            prev = self._last_update_id.get(symbol)
            if prev is not None and last_id < prev:
                return None
            self._last_update_id[symbol] = last_id

        bid_map: SideMap = {p: s for p, s in bids}
        ask_map: SideMap = {p: s for p, s in asks}
        bid = best_bid(bid_map)
        ask = best_ask(ask_map)
        if bid is None or ask is None or bid <= 0 or ask <= 0 or bid >= ask:
            return None

        self._bids[symbol] = bid_map
        self._asks[symbol] = ask_map
        recv = recv_ts_ms if recv_ts_ms is not None else now_ms()
        # lastUpdateId is a sequence, not ms — never store it as exchange_ts_ms.
        book = BybitBookTick(
            pair_id=pair_id,
            symbol=symbol,
            exchange_ts_ms=recv,
            recv_ts_ms=recv,
            bid=bid,
            ask=ask,
            bid_de_multiplied=multiplied_price(bid, mult),
            ask_de_multiplied=multiplied_price(ask, mult),
            multiplier=mult,
            gap=gap,
        )
        # Keep bookTicker L1 for the L1 journal when present, but always refresh
        # timestamps so depth_tick does not stamp VWAP with a stalled bookTicker.
        ticker = self._last_book.get(symbol)
        if ticker is not None:
            book = BybitBookTick(
                pair_id=pair_id,
                symbol=symbol,
                exchange_ts_ms=recv,
                recv_ts_ms=recv,
                bid=ticker.bid,
                ask=ticker.ask,
                bid_de_multiplied=ticker.bid_de_multiplied,
                ask_de_multiplied=ticker.ask_de_multiplied,
                multiplier=mult,
                gap=gap or ticker.gap,
            )
        self._last_book[symbol] = book
        return book

    def depth_tick(self, symbol: str) -> BybitDepthTick | None:
        symbol = symbol.upper()
        book = self._last_book.get(symbol)
        mult = self._ui_multiplier_by_symbol.get(symbol)
        bids = self._bids.get(symbol)
        asks = self._asks.get(symbol)
        if book is None or mult is None or not bids or not asks:
            return None
        bid_lv_dm = multiplied_levels(sorted_bid_levels(bids), mult)
        ask_lv_dm = multiplied_levels(sorted_ask_levels(asks), mult)
        if not bid_lv_dm or not ask_lv_dm:
            return None
        return BybitDepthTick(
            pair_id=book.pair_id,
            symbol=book.symbol,
            exchange_ts_ms=book.exchange_ts_ms,
            recv_ts_ms=book.recv_ts_ms,
            bid=book.bid,
            ask=book.ask,
            bid_de_multiplied=book.bid_de_multiplied,
            ask_de_multiplied=book.ask_de_multiplied,
            multiplier=mult,
            depth_levels=max(len(bid_lv_dm), len(ask_lv_dm)),
            buckets_usd=self._buckets_usd,
            bid_vwap_dm=tuple(vwap_curve(bid_lv_dm, list(self._buckets_usd))),
            ask_vwap_dm=tuple(vwap_curve(ask_lv_dm, list(self._buckets_usd))),
            gap=book.gap,
        )
