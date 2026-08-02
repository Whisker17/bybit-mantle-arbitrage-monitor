"""Async REST poller for CEX 24h volume (WHI-777)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal
from urllib.parse import urljoin

import httpx

from monitor.cex_volume.parse import (
    CexVolumeParseError,
    parse_binance_ticker_24hr,
    parse_bybit_tickers,
)
from monitor.quotes import CexVolumeTick, now_ms

logger = logging.getLogger(__name__)

OnVolume = Callable[[list[CexVolumeTick]], Awaitable[None]]
Venue = Literal["bybit", "binance"]


class CexVolumePoller:
    """Poll Bybit or Binance public tickers and emit ``CexVolumeTick`` batches."""

    def __init__(
        self,
        *,
        venue: Venue,
        rest_base_url: str,
        pair_id_by_symbol: dict[str, str],
        on_volume: OnVolume,
        poll_interval_s: float = 60.0,
        http_timeout_s: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not pair_id_by_symbol:
            raise ValueError("pair_id_by_symbol must be non-empty")
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be > 0")
        self.venue = venue
        self.rest_base_url = rest_base_url.rstrip("/") + "/"
        self.pair_id_by_symbol = {
            k.upper(): v for k, v in pair_id_by_symbol.items()
        }
        self.on_volume = on_volume
        self.poll_interval_s = poll_interval_s
        self.http_timeout_s = http_timeout_s
        self._client = client
        self._owns_client = client is None
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        client = self._client or httpx.AsyncClient(
            timeout=self.http_timeout_s,
            headers={"user-agent": "xstocks-monitor/cex-volume"},
        )
        try:
            while not self._stop.is_set():
                try:
                    ticks = await self._poll_once(client)
                    if ticks:
                        await self.on_volume(ticks)
                except Exception:
                    logger.exception(
                        "cex volume poll failed venue=%s base=%s",
                        self.venue,
                        self.rest_base_url,
                    )
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.poll_interval_s
                    )
                except TimeoutError:
                    continue
        finally:
            if self._owns_client:
                await client.aclose()

    async def _poll_once(self, client: httpx.AsyncClient) -> list[CexVolumeTick]:
        poll_ts = now_ms()
        if self.venue == "bybit":
            url = urljoin(self.rest_base_url, "v5/market/tickers")
            resp = await client.get(url, params={"category": "spot"})
            resp.raise_for_status()
            parsed = parse_bybit_tickers(
                resp.json(), pair_id_by_symbol=self.pair_id_by_symbol
            )
        else:
            url = urljoin(self.rest_base_url, "api/v3/ticker/24hr")
            # Request only our symbols when the list is short; fall back to full book.
            symbols = sorted(self.pair_id_by_symbol.keys())
            if len(symbols) == 1:
                resp = await client.get(url, params={"symbol": symbols[0]})
            elif len(symbols) <= 50:
                # Binance accepts a JSON array of symbols.
                import json

                resp = await client.get(
                    url, params={"symbols": json.dumps(symbols, separators=(",", ":"))}
                )
            else:
                resp = await client.get(url)
            resp.raise_for_status()
            parsed = parse_binance_ticker_24hr(
                resp.json(), pair_id_by_symbol=self.pair_id_by_symbol
            )

        recv_ts = now_ms()
        ticks = [
            CexVolumeTick(
                pair_id=pair_id,
                symbol=symbol,
                poll_ts_ms=poll_ts,
                recv_ts_ms=recv_ts,
                volume_quote_24h=vol,
                trade_count_24h=count,
                source=self.venue,
            )
            for pair_id, symbol, vol, count in parsed
        ]
        if not ticks:
            logger.warning(
                "cex volume poll returned 0 matches venue=%s wanted=%d",
                self.venue,
                len(self.pair_id_by_symbol),
            )
        else:
            logger.debug(
                "cex volume poll ok venue=%s pairs=%d",
                self.venue,
                len(ticks),
            )
        return ticks


# Re-export for tests that construct parse errors via poller imports.
__all__ = ["CexVolumePoller", "CexVolumeParseError"]
