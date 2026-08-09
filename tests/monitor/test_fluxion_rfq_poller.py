"""WHI-974: RFQ HTTP errors (e.g. 451) leave journal rows; failover still works."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from monitor.fluxion.rfq import (
    RfqPoller,
    is_rfq_http_error,
    parse_rfq_response,
    rfq_http_status_bucket,
)
from monitor.quotes import FluxionRfqQuoteTick
from monitor.storage import JournalReader, SqliteStore
from monitor.storage.reader import rfq_quote_coverage
from monitor.symbols.models import AmmPool, BybitSymbol, FluxionSide, Pair, RfqConfig


def _pair() -> Pair:
    return Pair(
        id="TSLAx",
        name="Tesla",
        low_liquidity=False,
        bybit=BybitSymbol(
            symbol="TSLAXUSDT",
            base_coin="TSLAX",
            multiplier=Decimal(1),
            multiplier_source="test",
        ),
        fluxion=FluxionSide(
            native_token="0x" + "33" * 20,
            native_decimals=18,
            quote_token="USDC",
            quote_token_address="0x" + "44" * 20,
            wrapper_token="0x" + "22" * 20,
            amm=AmmPool(
                kind="v3",
                pool="0x" + "11" * 20,
                fee=3000,
                est_liquidity_usd=100_000.0,
            ),
        ),
    )


def _rfq_cfg() -> RfqConfig:
    return RfqConfig(
        mode="pollable_quote",
        quote_url="https://fluxion.network/api/limit-order/quote",
        proxy_quote_url="https://fluxion-proxy-api-production.up.railway.app/quote",
        request_type="EXACT_INPUT",
        quote_asset="USDC",
        rate_limit_per_minute=60,
        min_poll_interval_s=11,
        settlement="limit_order_protocol",
    )


def test_parse_rfq_451_error_row() -> None:
    """Non-200/204 → available=False, price null, status preserved (WHI-974)."""
    tick = parse_rfq_response(
        pair_id="TSLAx",
        token_in="0x44",
        token_out="0x33",
        amount_in="100000000",
        poll_ts_ms=1,
        recv_ts_ms=2,
        http_status=451,
        body=None,
        side_hint="buy_native",
    )
    assert tick.available is False
    assert tick.price is None
    assert tick.amount_out is None
    assert tick.http_status == 451
    assert tick.side == "buy_native"
    assert is_rfq_http_error(451)
    assert not is_rfq_http_error(200)
    assert not is_rfq_http_error(204)
    assert rfq_http_status_bucket(451) == "error"
    assert rfq_http_status_bucket(200) == "ok"
    assert rfq_http_status_bucket(204) == "no_quote"


def test_poll_one_persists_451_then_failover_success() -> None:
    """Primary 451 must leave a row; proxy 200 still yields a usable quote."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = urlparse(str(request.url)).netloc
        calls.append(host)
        if "fluxion.network" in host:
            return httpx.Response(451, text="Unavailable For Legal Reasons")
        return httpx.Response(
            200,
            json={
                "side": "buy",
                "price": "250.5",
                "amountOut": "400000000000000000",
                "requestId": "r1",
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    pair = _pair()
    poller = RfqPoller(
        pairs=[pair],
        rfq=_rfq_cfg(),
        amount_usdc_raw="100000000",
        amount_native_raw="1000000000000000000",
        client=client,
    )
    ticks = poller.poll_one(pair, "buy_native")
    poller.close()

    assert len(ticks) == 2
    err, ok = ticks
    assert err.http_status == 451
    assert err.available is False
    assert err.price is None
    assert err.side == "buy_native"
    assert ok.http_status == 200
    assert ok.available is True
    assert ok.price == Decimal("250.5")
    assert "fluxion.network" in calls[0]
    assert "railway" in calls[1]


def test_poll_one_both_urls_451_only_error_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(451, text="blocked")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    pair = _pair()
    poller = RfqPoller(
        pairs=[pair],
        rfq=_rfq_cfg(),
        amount_usdc_raw="100000000",
        amount_native_raw="1000000000000000000",
        client=client,
    )
    ticks = poller.poll_one(pair, "sell_native")
    poller.close()

    assert len(ticks) >= 1
    assert all(t.http_status == 451 for t in ticks)
    assert all(not t.available for t in ticks)
    assert all(t.side == "sell_native" for t in ticks)


def test_coverage_stats_exclude_errors_from_availability(tmp_path: Path) -> None:
    """Availability rate uses only 200/204 rows; errors counted separately."""
    db = tmp_path / "r.db"
    store = SqliteStore(db)
    tok_in = "0x" + "44" * 20
    tok_out = "0x" + "33" * 20

    def tick(
        *,
        poll_ts_ms: int,
        http_status: int,
        available: bool = False,
        price: Decimal | None = None,
        amount_out: str | None = None,
    ) -> FluxionRfqQuoteTick:
        return FluxionRfqQuoteTick(
            pair_id="TSLAx",
            poll_ts_ms=poll_ts_ms,
            recv_ts_ms=poll_ts_ms + 1,
            token_in=tok_in,
            token_out=tok_out,
            amount_in="100000000",
            amount_out=amount_out,
            price=price,
            side="buy_native",
            request_id=None,
            http_status=http_status,
            available=available,
            gap=False,
        )

    store.insert_rfq_quotes(
        [
            tick(poll_ts_ms=1, http_status=200, available=True, price=Decimal("1"), amount_out="1"),
            tick(poll_ts_ms=2, http_status=204),
            tick(poll_ts_ms=3, http_status=451),
            tick(poll_ts_ms=4, http_status=451),
            tick(poll_ts_ms=5, http_status=200, available=True, price=Decimal("1"), amount_out="1"),
        ]
    )
    store.close()

    with JournalReader(db) as reader:
        cov = rfq_quote_coverage(reader, since_ms=0)
    assert cov.total_rows == 5
    assert cov.ok_200 == 2
    assert cov.no_quote_204 == 1
    assert cov.error_rows == 2
    assert cov.error_rate == pytest.approx(0.4)
    # Availability among non-error polls: 2 available / (2+1) = 2/3
    assert cov.availability_among_reachable == pytest.approx(2 / 3)
