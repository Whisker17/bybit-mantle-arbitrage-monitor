"""Bybit public spot WS collector with reconnect + gap marking."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal
from typing import Any

from monitor.bybit.parse import (
    build_subscribe_args,
    parse_public_trade_message,
    parse_ticker_message,
)
from monitor.quotes import BybitBookTick, BybitTradeTick, CollectorGap, now_ms

logger = logging.getLogger(__name__)

OnBook = Callable[[BybitBookTick], Awaitable[None] | None]
OnTrade = Callable[[BybitTradeTick], Awaitable[None] | None]
OnGap = Callable[[CollectorGap], Awaitable[None] | None]


async def _maybe_await(result: Awaitable[None] | None) -> None:
    if result is not None:
        await result


class BybitWsCollector:
    """Long-running Bybit v5 public stream; pure realtime, no backfill."""

    def __init__(
        self,
        *,
        ws_url: str,
        symbols: list[str],
        pair_id_by_symbol: Mapping[str, str],
        multiplier_by_symbol: Mapping[str, Decimal],
        on_book: OnBook,
        on_trade: OnTrade,
        on_gap: OnGap | None = None,
        book_topic_prefix: str = "tickers",
        trade_topic_prefix: str = "publicTrade",
        reconnect_min_s: float = 1.0,
        reconnect_max_s: float = 60.0,
        post_reconnect_gap_s: float = 5.0,
        ping_interval_s: float = 20.0,
        connect: Callable[[str], Any] | None = None,
    ) -> None:
        self.ws_url = ws_url
        self.symbols = [s.upper() for s in symbols]
        self.pair_id_by_symbol = dict(pair_id_by_symbol)
        self.multiplier_by_symbol = dict(multiplier_by_symbol)
        self.on_book = on_book
        self.on_trade = on_trade
        self.on_gap = on_gap
        self.book_topic_prefix = book_topic_prefix
        self.trade_topic_prefix = trade_topic_prefix
        self.reconnect_min_s = reconnect_min_s
        self.reconnect_max_s = reconnect_max_s
        self.post_reconnect_gap_s = post_reconnect_gap_s
        self.ping_interval_s = ping_interval_s
        self._connect = connect
        self._stop = asyncio.Event()
        self._gap_until_ms: int = 0
        self._disconnect_at_ms: int | None = None
        self._ever_connected = False

    def request_stop(self) -> None:
        self._stop.set()

    def _in_gap_window(self) -> bool:
        return now_ms() < self._gap_until_ms

    async def run(self) -> None:
        delay = self.reconnect_min_s
        while not self._stop.is_set():
            try:
                await self._session()
                # Clean close (server force-close without exception) still counts
                # as a disconnect for gap marking when we reconnect.
                self._note_disconnect("session ended")
                delay = self.reconnect_min_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any transport fault
                logger.warning("bybit ws session error: %s", exc)
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
        logger.info("bybit ws disconnect noted: %s", detail)

    async def _emit_reconnect_gap(self) -> None:
        if self._disconnect_at_ms is None:
            return
        end = now_ms()
        self._gap_until_ms = end + int(self.post_reconnect_gap_s * 1000)
        if self.on_gap is not None:
            gap = CollectorGap(
                source="bybit_ws",
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

        async with connect(self.ws_url) as ws:
            was_reconnect = self._ever_connected and self._disconnect_at_ms is not None
            self._ever_connected = True
            if was_reconnect or self._disconnect_at_ms is not None:
                await self._emit_reconnect_gap()
            args = build_subscribe_args(
                self.symbols,
                book_prefix=self.book_topic_prefix,
                trade_prefix=self.trade_topic_prefix,
            )
            for i in range(0, len(args), 10):
                chunk = args[i : i + 10]
                await ws.send(json.dumps({"op": "subscribe", "args": chunk}))

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
        while not self._stop.is_set():
            await asyncio.sleep(self.ping_interval_s)
            try:
                await ws.send(json.dumps({"op": "ping"}))
            except Exception:  # noqa: BLE001
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
        op = payload.get("op")
        if op in ("pong", "ping", "subscribe"):
            return
        topic = str(payload.get("topic") or "")
        recv = now_ms()
        use_gap = self._in_gap_window()
        if topic.startswith(f"{self.book_topic_prefix}."):
            tick = parse_ticker_message(
                payload,
                pair_id_by_symbol=self.pair_id_by_symbol,
                multiplier_by_symbol=self.multiplier_by_symbol,
                recv_ts_ms=recv,
                gap=use_gap,
            )
            if tick is not None:
                await _maybe_await(self.on_book(tick))
            return
        if topic.startswith(f"{self.trade_topic_prefix}."):
            trades = parse_public_trade_message(
                payload,
                pair_id_by_symbol=self.pair_id_by_symbol,
                multiplier_by_symbol=self.multiplier_by_symbol,
                recv_ts_ms=recv,
                gap=use_gap,
            )
            for t in trades:
                await _maybe_await(self.on_trade(t))
