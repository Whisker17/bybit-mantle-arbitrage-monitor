"""Binance public combined-stream collector with reconnect + gap marking."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse, urlunparse

from monitor.binance.depth import BinanceDepthTracker
from monitor.binance.parse import (
    DEFAULT_BOOK_STREAM,
    DEFAULT_DEPTH_STREAM,
    DEFAULT_TRADE_STREAM,
    build_combined_stream_path,
    parse_agg_trade_message,
    stream_kind,
)
from monitor.bybit.depth import DEFAULT_DEPTH_BUCKETS_USD
from monitor.bybit.ws import DepthEmitThrottle
from monitor.quotes import BybitBookTick, BybitDepthTick, BybitTradeTick, CollectorGap, now_ms

logger = logging.getLogger(__name__)

OnBook = Callable[[BybitBookTick], Awaitable[None] | None]
OnDepth = Callable[[BybitDepthTick], Awaitable[None] | None]
OnTrade = Callable[[BybitTradeTick], Awaitable[None] | None]
OnGap = Callable[[CollectorGap], Awaitable[None] | None]


async def _maybe_await(result: Awaitable[None] | None) -> None:
    if result is not None:
        await result


def join_ws_url(base_url: str, stream_path: str) -> str:
    """Join a WS base with ``/stream?streams=…`` (vision host path)."""
    base = base_url.rstrip("/")
    parsed = urlparse(base)
    if "?" in stream_path:
        path, query = stream_path.split("?", 1)
    else:
        path, query = stream_path, ""
    if not path.startswith("/"):
        path = "/" + path
    # Vision host expects: wss://host/stream?streams=… (ignore any base path).
    return urlunparse((parsed.scheme, parsed.netloc, path, "", query, ""))


class BinanceWsCollector:
    """Long-running Binance public combined stream; pure realtime, no backfill.

    Endpoint host comes from config (vision hosts for US VPS — M7-1). No proxy
    hardcoding: operators set ``ws_base_url`` / env as needed.
    """

    def __init__(
        self,
        *,
        ws_base_url: str,
        symbols: list[str],
        pair_id_by_symbol: Mapping[str, str],
        ui_multiplier_by_symbol: Mapping[str, Decimal],
        on_book: OnBook,
        on_trade: OnTrade,
        on_gap: OnGap | None = None,
        on_depth: OnDepth | None = None,
        book_stream: str = DEFAULT_BOOK_STREAM,
        depth_stream: str = DEFAULT_DEPTH_STREAM,
        trade_stream: str = DEFAULT_TRADE_STREAM,
        reconnect_min_s: float = 1.0,
        reconnect_max_s: float = 60.0,
        post_reconnect_gap_s: float = 5.0,
        ping_interval_s: float = 20.0,
        depth_enabled: bool = True,
        depth_buckets_usd: Sequence[Decimal] | None = None,
        depth_emit_interval_ms: int = 1000,
        depth_mid_change_bps: Decimal = Decimal("1"),
        connect: Callable[[str], Any] | None = None,
    ) -> None:
        self.ws_base_url = ws_base_url.rstrip("/")
        self.symbols = [s.upper() for s in symbols]
        self.pair_id_by_symbol = {k.upper(): v for k, v in pair_id_by_symbol.items()}
        self.ui_multiplier_by_symbol = {
            k.upper(): v for k, v in ui_multiplier_by_symbol.items()
        }
        self.on_book = on_book
        self.on_trade = on_trade
        self.on_gap = on_gap
        self.on_depth = on_depth
        self.book_stream = book_stream
        self.depth_stream = depth_stream
        self.trade_stream = trade_stream
        self.reconnect_min_s = reconnect_min_s
        self.reconnect_max_s = reconnect_max_s
        self.post_reconnect_gap_s = post_reconnect_gap_s
        self.ping_interval_s = ping_interval_s
        self.depth_enabled = depth_enabled
        self.depth_buckets_usd = (
            tuple(depth_buckets_usd)
            if depth_buckets_usd is not None
            else DEFAULT_DEPTH_BUCKETS_USD
        )
        self._depth_throttle = DepthEmitThrottle(
            emit_interval_ms=depth_emit_interval_ms,
            mid_change_bps=depth_mid_change_bps,
        )
        self._connect = connect
        self._stop = asyncio.Event()
        self._gap_until_ms: int = 0
        self._disconnect_at_ms: int | None = None
        self._ever_connected = False
        self._book = self._new_tracker()
        # Symbols that have already emitted at least one L1 (bookTicker or depth seed).
        self._seeded_l1: set[str] = set()

    def _new_tracker(self) -> BinanceDepthTracker:
        return BinanceDepthTracker(
            pair_id_by_symbol=self.pair_id_by_symbol,
            ui_multiplier_by_symbol=self.ui_multiplier_by_symbol,
            buckets_usd=self.depth_buckets_usd,
        )

    def request_stop(self) -> None:
        self._stop.set()

    def _in_gap_window(self) -> bool:
        return now_ms() < self._gap_until_ms

    def stream_url(self) -> str:
        path = build_combined_stream_path(
            self.symbols,
            book_stream=self.book_stream,
            depth_stream=self.depth_stream if self.depth_enabled else None,
            trade_stream=self.trade_stream,
            depth_enabled=self.depth_enabled,
        )
        return join_ws_url(self.ws_base_url, path)

    async def run(self) -> None:
        delay = self.reconnect_min_s
        while not self._stop.is_set():
            try:
                await self._session()
                self._note_disconnect("session ended")
                delay = self.reconnect_min_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("binance ws session error: %s", exc)
                self._note_disconnect(str(exc))
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
                delay = min(delay * 2, self.reconnect_max_s)

    def _note_disconnect(self, detail: str) -> None:
        if not self._ever_connected:
            return
        self._disconnect_at_ms = now_ms()
        logger.info("binance ws disconnect noted: %s", detail)

    async def _emit_reconnect_gap(self) -> None:
        if self._disconnect_at_ms is None:
            return
        end = now_ms()
        self._gap_until_ms = end + int(self.post_reconnect_gap_s * 1000)
        if self.on_gap is not None:
            gap = CollectorGap(
                source="binance_ws",
                gap_start_ms=self._disconnect_at_ms,
                gap_end_ms=end,
                detail="websocket reconnect",
            )
            await _maybe_await(self.on_gap(gap))
        self._disconnect_at_ms = None

    async def _session(self) -> None:
        connect = self._connect
        if connect is None:
            import websockets  # lazy so unit tests can inject a fake

            connect = websockets.connect

        url = self.stream_url()
        async with connect(url) as ws:
            self._ever_connected = True
            self._book = self._new_tracker()
            self._seeded_l1 = set()
            self._depth_throttle = DepthEmitThrottle(
                emit_interval_ms=self._depth_throttle.emit_interval_ms,
                mid_change_bps=self._depth_throttle.mid_change_bps,
            )
            if self._disconnect_at_ms is not None:
                await self._emit_reconnect_gap()

            ping_task = asyncio.create_task(self._ping_loop(ws))
            try:
                async for raw in ws:
                    if self._stop.is_set():
                        break
                    await self._handle_raw(raw)
            finally:
                ping_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ping_task

    async def _ping_loop(self, ws: Any) -> None:
        # Probe half-open sockets: websockets.ping() returns an awaitable that
        # resolves when the pong arrives. On failure, close the socket so
        # ``async for`` ends and run() reconnects with a CollectorGap.
        while not self._stop.is_set():
            await asyncio.sleep(self.ping_interval_s)
            try:
                ping = getattr(ws, "ping", None)
                if not callable(ping):
                    return
                waiter = ping()
                if hasattr(waiter, "__await__"):
                    await waiter
            except Exception:  # noqa: BLE001
                close = getattr(ws, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        await close()
                return

    async def _handle_raw(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        # Combined stream envelope: {stream, data}; single stream is flat event.
        stream = str(payload.get("stream") or "")
        data = payload.get("data") if "data" in payload else payload
        if not isinstance(data, dict):
            return
        kind = stream_kind(stream) if stream else _infer_kind(data)
        recv = now_ms()
        use_gap = self._in_gap_window()
        envelope = payload if "data" in payload else {"data": data, "stream": stream}

        if kind == "book":
            book = self._book.apply_book_ticker(
                envelope, recv_ts_ms=recv, gap=use_gap, stream=stream
            )
            if book is not None:
                self._seeded_l1.add(book.symbol)
                await _maybe_await(self.on_book(book))
            return

        if kind == "depth":
            book = self._book.apply_depth(
                envelope, recv_ts_ms=recv, gap=use_gap, stream=stream
            )
            if book is None:
                return
            # L1 is bookTicker's job; depth only journals throttled VWAP curves.
            # Fallback: first depth snapshot also seeds L1 if bookTicker is late.
            if book.symbol not in self._seeded_l1:
                self._seeded_l1.add(book.symbol)
                await _maybe_await(self.on_book(book))
            if (
                self.depth_enabled
                and self.on_depth is not None
                and self._depth_throttle.should_emit(book)
            ):
                depth = self._book.depth_tick(book.symbol)
                if depth is not None:
                    self._depth_throttle.mark_emitted(book)
                    await _maybe_await(self.on_depth(depth))
            return

        if kind == "trade":
            trades = parse_agg_trade_message(
                envelope,
                pair_id_by_symbol=self.pair_id_by_symbol,
                ui_multiplier_by_symbol=self.ui_multiplier_by_symbol,
                recv_ts_ms=recv,
                gap=use_gap,
                stream=stream,
            )
            for t in trades:
                await _maybe_await(self.on_trade(t))


def _infer_kind(data: dict[str, Any]) -> str:
    event = str(data.get("e") or "").lower()
    if event == "bookticker" or ("b" in data and "a" in data and "B" in data and "A" in data):
        return "book"
    if "bids" in data or "asks" in data or event == "depthupdate":
        return "depth"
    if event in ("aggtrade", "trade") or ("p" in data and "q" in data and "m" in data):
        return "trade"
    return "unknown"
