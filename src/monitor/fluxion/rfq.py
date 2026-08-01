"""Fluxion xChange Atomic RFQ quote polling (mode=pollable_quote)."""

from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from monitor.quotes import FluxionRfqQuoteTick
from monitor.symbols.models import Pair, RfqConfig

logger = logging.getLogger(__name__)


def parse_rfq_response(
    *,
    pair_id: str,
    token_in: str,
    token_out: str,
    amount_in: str,
    poll_ts_ms: int,
    recv_ts_ms: int,
    http_status: int,
    body: dict[str, Any] | None,
    gap: bool = False,
) -> FluxionRfqQuoteTick:
    """Map HTTP status + JSON body to a tick. 204 → available=False, not an error."""
    if http_status == 204 or body is None:
        return FluxionRfqQuoteTick(
            pair_id=pair_id,
            poll_ts_ms=poll_ts_ms,
            recv_ts_ms=recv_ts_ms,
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            amount_out=None,
            price=None,
            side=None,
            request_id=None,
            http_status=http_status,
            available=False,
            gap=gap,
        )
    price: Decimal | None = None
    raw_price = body.get("price")
    if raw_price is not None:
        try:
            price = Decimal(str(raw_price))
        except InvalidOperation:
            price = None
    amount_out = body.get("amountOut")
    return FluxionRfqQuoteTick(
        pair_id=pair_id,
        poll_ts_ms=poll_ts_ms,
        recv_ts_ms=recv_ts_ms,
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        amount_out=None if amount_out is None else str(amount_out),
        price=price,
        side=None if body.get("side") is None else str(body.get("side")),
        request_id=None if body.get("requestId") is None else str(body.get("requestId")),
        http_status=http_status,
        available=http_status == 200 and price is not None,
        gap=gap,
    )


class RfqPoller:
    """Round-robin EXACT_INPUT quote polls respecting global rate limit."""

    def __init__(
        self,
        *,
        pairs: list[Pair],
        rfq: RfqConfig,
        amount_usdc_raw: str,
        prefer_primary_url: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self.pairs = list(pairs)
        self.rfq = rfq
        self.amount_usdc_raw = amount_usdc_raw
        self.prefer_primary_url = prefer_primary_url
        self._client = client or httpx.Client(
            timeout=20.0,
            headers={"user-agent": "monitor/0.1 (bybit-mantle-arbitrage-monitor)"},
        )
        self._owns_client = client is None
        self._idx = 0

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def poll_one(self, pair: Pair, *, gap: bool = False) -> FluxionRfqQuoteTick:
        """Buy native xStock with USDC (tokenIn=USDC, tokenOut=native)."""
        token_in = pair.fluxion.quote_token_address
        token_out = pair.fluxion.native_token
        payload = {
            "tokenIn": token_in,
            "tokenOut": token_out,
            "amount": self.amount_usdc_raw,
            "type": self.rfq.request_type,
        }
        urls = [str(self.rfq.quote_url), str(self.rfq.proxy_quote_url)]
        if not self.prefer_primary_url:
            urls = list(reversed(urls))

        poll_ts = _now_ms()
        last_status = 0
        last_body: dict[str, Any] | None = None
        for url in urls:
            try:
                r = self._client.post(url, json=payload)
                last_status = r.status_code
                if r.status_code == 204:
                    last_body = None
                    break
                if r.status_code == 200:
                    try:
                        last_body = r.json()
                    except Exception:  # noqa: BLE001
                        last_body = None
                    break
                logger.warning(
                    "rfq quote HTTP %s from %s for %s", r.status_code, url, pair.id
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("rfq quote transport error %s for %s: %s", url, pair.id, exc)
                last_status = 0
        recv = _now_ms()
        return parse_rfq_response(
            pair_id=pair.id,
            token_in=token_in,
            token_out=token_out,
            amount_in=self.amount_usdc_raw,
            poll_ts_ms=poll_ts,
            recv_ts_ms=recv,
            http_status=last_status or 0,
            body=last_body if isinstance(last_body, dict) else None,
            gap=gap,
        )

    def next_pair(self) -> Pair:
        if not self.pairs:
            raise RuntimeError("no pairs to poll")
        pair = self.pairs[self._idx % len(self.pairs)]
        self._idx += 1
        return pair

    def poll_next(self, *, gap: bool = False) -> FluxionRfqQuoteTick:
        return self.poll_one(self.next_pair(), gap=gap)


def _now_ms() -> int:
    return int(time.time() * 1000)
