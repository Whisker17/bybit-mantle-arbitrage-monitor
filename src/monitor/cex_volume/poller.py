"""Async REST poller for CEX 24h volume (WHI-777 + WHI-974 geo-block)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx

from monitor.cex_volume.parse import (
    parse_binance_ticker_24hr,
    parse_bybit_tickers,
)
from monitor.quotes import CexVolumeTick, now_ms

logger = logging.getLogger(__name__)

OnVolume = Callable[[list[CexVolumeTick]], Awaitable[None]]
Venue = Literal["bybit", "binance"]

# Persistent REST block (e.g. Bybit 403 from AS3635 US hosts). Not retriable
# via alternate public hosts measured in WHI-974.
GEO_BLOCK_STATUSES = frozenset({403, 451})

# Meta keys written by the collector so API/UI can source geo_blocked from fact.
META_CEX_VOLUME_STATUS = "cex_volume_status"  # ok | geo_blocked | error
META_CEX_VOLUME_VENUE = "cex_volume_venue"
META_CEX_VOLUME_HOST = "cex_volume_host"
META_CEX_VOLUME_HTTP_STATUS = "cex_volume_http_status"
META_CEX_VOLUME_FIRST_MS = "cex_volume_blocked_first_ms"
META_CEX_VOLUME_LAST_MS = "cex_volume_blocked_last_ms"
META_CEX_VOLUME_DETAIL = "cex_volume_detail"

CexVolumeStatus = Literal["ok", "geo_blocked", "error"]

OnCexVolumeStatus = Callable[
    [dict[str, str]], Coroutine[Any, Any, None] | None
]


class CexVolumePoller:
    """Poll Bybit or Binance public tickers and emit ``CexVolumeTick`` batches.

    WHI-974: a persistent geo-block HTTP status (403/451) is logged **once** on
    transition into the blocked state (WARNING, no traceback spam), then quiet
    until recovery or a different failure mode. Status is published via
    ``on_status`` so the journal meta / health surface can feed the UI reason.
    """

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
        on_status: OnCexVolumeStatus | None = None,
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
        self._on_status = on_status
        # None = never observed.
        self._state: CexVolumeStatus | None = None
        self._blocked_first_ms: int | None = None

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def state(self) -> CexVolumeStatus | None:
        return self._state

    def _host(self) -> str:
        return urlparse(self.rest_base_url).netloc or self.rest_base_url

    async def _publish_status(
        self,
        status: CexVolumeStatus,
        *,
        http_status: int | None = None,
        detail: str = "",
    ) -> None:
        ts = now_ms()
        if status == "geo_blocked" and self._blocked_first_ms is None:
            self._blocked_first_ms = ts
        if status == "ok":
            self._blocked_first_ms = None
        meta = {
            META_CEX_VOLUME_STATUS: status,
            META_CEX_VOLUME_VENUE: self.venue,
            META_CEX_VOLUME_HOST: self._host(),
            META_CEX_VOLUME_HTTP_STATUS: "" if http_status is None else str(http_status),
            META_CEX_VOLUME_FIRST_MS: (
                "" if self._blocked_first_ms is None else str(self._blocked_first_ms)
            ),
            META_CEX_VOLUME_LAST_MS: str(ts) if status != "ok" else "",
            META_CEX_VOLUME_DETAIL: detail[:500],
        }
        if self._on_status is None:
            return
        result = self._on_status(meta)
        if asyncio.iscoroutine(result):
            await result

    async def _transition(
        self,
        new_state: CexVolumeStatus,
        *,
        http_status: int | None = None,
        detail: str = "",
        exc: BaseException | None = None,
    ) -> None:
        prev = self._state
        if new_state == prev and new_state in ("geo_blocked", "error"):
            # Stay quiet on repeated failures; refresh last-seen meta only.
            await self._publish_status(
                new_state, http_status=http_status, detail=detail
            )
            return

        self._state = new_state
        if new_state == "geo_blocked":
            logger.warning(
                "cex volume REST geo-blocked venue=%s host=%s http=%s "
                "(will stay quiet until recovery; WS feeds unaffected)",
                self.venue,
                self._host(),
                http_status,
            )
        elif new_state == "ok" and prev in ("geo_blocked", "error"):
            logger.warning(
                "cex volume REST recovered venue=%s host=%s (was %s)",
                self.venue,
                self._host(),
                prev,
            )
        elif new_state == "error":
            if exc is not None:
                logger.exception(
                    "cex volume poll failed venue=%s base=%s",
                    self.venue,
                    self.rest_base_url,
                )
            else:
                logger.warning(
                    "cex volume poll failed venue=%s base=%s detail=%s",
                    self.venue,
                    self.rest_base_url,
                    detail,
                )
        await self._publish_status(new_state, http_status=http_status, detail=detail)

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
                    await self._transition("ok")
                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code
                    if code in GEO_BLOCK_STATUSES:
                        await self._transition(
                            "geo_blocked",
                            http_status=code,
                            detail=f"HTTP {code} {exc.response.reason_phrase}",
                        )
                    else:
                        await self._transition(
                            "error",
                            http_status=code,
                            detail=f"HTTP {code}",
                            exc=exc,
                        )
                except Exception as exc:
                    await self._transition(
                        "error",
                        detail=str(exc)[:200],
                        exc=exc,
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


__all__ = [
    "CexVolumePoller",
    "GEO_BLOCK_STATUSES",
    "META_CEX_VOLUME_DETAIL",
    "META_CEX_VOLUME_FIRST_MS",
    "META_CEX_VOLUME_HOST",
    "META_CEX_VOLUME_HTTP_STATUS",
    "META_CEX_VOLUME_LAST_MS",
    "META_CEX_VOLUME_STATUS",
    "META_CEX_VOLUME_VENUE",
]
