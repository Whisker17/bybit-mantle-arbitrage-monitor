"""WHI-777: CexVolumePoller URL/params + parse path (httpx mock transport)."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from monitor.cex_volume.poller import CexVolumePoller
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
