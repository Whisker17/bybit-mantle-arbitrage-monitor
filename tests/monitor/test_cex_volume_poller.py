"""WHI-777: CexVolumePoller URL/params + parse path (httpx mock transport).

WHI-974: persistent 403 is a quiet geo_blocked transition, not a traceback loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal

import httpx
import pytest

from monitor.cex_volume.poller import (
    META_CEX_VOLUME_HOST,
    META_CEX_VOLUME_STATUS,
    CexVolumePoller,
)
from monitor.quotes import CexVolumeTick


@pytest.mark.asyncio
async def test_bybit_poll_once_builds_ticks() -> None:
    payload = {
        "retCode": 0,
        "result": {
            "list": [
                {"symbol": "TSLAXUSDT", "turnover24h": "1234.5"},
                {"symbol": "SKIP", "turnover24h": "1"},
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "v5/market/tickers" in str(request.url)
        assert request.url.params.get("category") == "spot"
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        poller = CexVolumePoller(
            venue="bybit",
            rest_base_url="https://api.bybit.com",
            pair_id_by_symbol={"TSLAXUSDT": "TSLAx"},
            on_volume=lambda ticks: None,  # type: ignore[arg-type, return-value]
            client=client,
        )
        ticks = await poller._poll_once(client)
    assert len(ticks) == 1
    t = ticks[0]
    assert isinstance(t, CexVolumeTick)
    assert t.pair_id == "TSLAx"
    assert t.volume_quote_24h == Decimal("1234.5")
    assert t.source == "bybit"


@pytest.mark.asyncio
async def test_binance_poll_once_multi_symbol() -> None:
    body = [
        {"symbol": "AUSDT", "quoteVolume": "10", "count": 2},
        {"symbol": "BUSDT", "quoteVolume": "20", "count": 3},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api/v3/ticker/24hr" in str(request.url)
        symbols = request.url.params.get("symbols")
        assert symbols is not None
        assert set(json.loads(symbols)) == {"AUSDT", "BUSDT"}
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        poller = CexVolumePoller(
            venue="binance",
            rest_base_url="https://data-api.binance.vision",
            pair_id_by_symbol={"AUSDT": "A", "BUSDT": "B"},
            on_volume=lambda ticks: None,  # type: ignore[arg-type, return-value]
            client=client,
        )
        ticks = await poller._poll_once(client)
    assert {t.pair_id for t in ticks} == {"A", "B"}
    by_id = {t.pair_id: t for t in ticks}
    assert by_id["B"].volume_quote_24h == Decimal("20")
    assert by_id["B"].trade_count_24h == 3


@pytest.mark.asyncio
async def test_bybit_403_logs_once_then_quiet(caplog: pytest.LogCaptureFixture) -> None:
    """Persistent 403 → geo_blocked once; no exception traceback spam (WHI-974)."""
    statuses: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        poller = CexVolumePoller(
            venue="bybit",
            rest_base_url="https://api.bybit.com",
            pair_id_by_symbol={"TSLAXUSDT": "TSLAx"},
            on_volume=lambda ticks: asyncio.sleep(0),
            poll_interval_s=0.01,
            client=client,
            on_status=lambda meta: statuses.append(dict(meta)),
        )

        async def _run_briefly() -> None:
            task = asyncio.create_task(poller.run())
            await asyncio.sleep(0.08)
            poller.request_stop()
            await task

        with caplog.at_level(logging.WARNING, logger="monitor.cex_volume.poller"):
            await _run_briefly()

    assert poller.state == "geo_blocked"
    assert statuses
    assert statuses[-1][META_CEX_VOLUME_STATUS] == "geo_blocked"
    assert "api.bybit.com" in statuses[-1][META_CEX_VOLUME_HOST]
    # One transition WARNING; no ERROR/traceback loop.
    warns = [
        r
        for r in caplog.records
        if r.name == "monitor.cex_volume.poller" and r.levelno >= logging.WARNING
    ]
    geo = [r for r in warns if "geo-blocked" in r.getMessage()]
    assert len(geo) == 1
    assert not any(r.exc_info for r in caplog.records)


@pytest.mark.asyncio
async def test_bybit_403_then_200_logs_recovery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 1:
            return httpx.Response(403, text="Forbidden")
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "result": {
                    "list": [{"symbol": "TSLAXUSDT", "turnover24h": "10"}]
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        poller = CexVolumePoller(
            venue="bybit",
            rest_base_url="https://api.bybit.com",
            pair_id_by_symbol={"TSLAXUSDT": "TSLAx"},
            on_volume=lambda ticks: asyncio.sleep(0),
            poll_interval_s=0.01,
            client=client,
        )

        async def _run_briefly() -> None:
            task = asyncio.create_task(poller.run())
            await asyncio.sleep(0.08)
            poller.request_stop()
            await task

        with caplog.at_level(logging.WARNING, logger="monitor.cex_volume.poller"):
            await _run_briefly()

    assert poller.state == "ok"
    msgs = [r.getMessage() for r in caplog.records if r.name == "monitor.cex_volume.poller"]
    assert any("geo-blocked" in m for m in msgs)
    assert any("recovered" in m for m in msgs)
