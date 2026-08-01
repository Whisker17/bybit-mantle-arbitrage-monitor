"""Fluxion xChange Atomic RFQ quote polling (mode=pollable_quote)."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx

from monitor.quotes import FluxionRfqQuoteTick, now_ms
from monitor.symbols.models import Pair, RfqConfig

logger = logging.getLogger(__name__)

RfqLeg = Literal["buy_native", "sell_native"]


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
    """Round-robin EXACT_INPUT quote polls respecting global rate limit.

    Each ``poll_next`` issues **one** HTTP quote. With ``poll_both_sides``, the
    schedule interleaves buy_native and sell_native legs so a full pair cycle is
    2N polls. Sleep between polls should be ``60 / rate_limit_per_minute`` so the
    global budget is filled (pairs.yaml: N=11 → 11s/pair/side at 60/min when
    both sides are on).
    """

    def __init__(
        self,
        *,
        pairs: list[Pair],
        rfq: RfqConfig,
        amount_usdc_raw: str,
        amount_native_raw: str,
        prefer_primary_url: bool = True,
        poll_both_sides: bool = True,
        http_timeout_s: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.pairs = list(pairs)
        self.rfq = rfq
        self.amount_usdc_raw = amount_usdc_raw
        self.amount_native_raw = amount_native_raw
        self.prefer_primary_url = prefer_primary_url
        self.poll_both_sides = poll_both_sides
        self._client = client or httpx.Client(
            timeout=http_timeout_s,
            headers={"user-agent": "monitor/0.1 (bybit-mantle-arbitrage-monitor)"},
        )
        self._owns_client = client is None
        self._idx = 0

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def poll_interval_s(self) -> float:
        """Seconds between successive HTTP polls to fill rate_limit_per_minute."""
        return 60.0 / float(self.rfq.rate_limit_per_minute)

    def _schedule_len(self) -> int:
        sides = 2 if self.poll_both_sides else 1
        return max(1, len(self.pairs) * sides)

    def next_job(self) -> tuple[Pair, RfqLeg]:
        if not self.pairs:
            raise RuntimeError("no pairs to poll")
        i = self._idx % self._schedule_len()
        self._idx += 1
        if self.poll_both_sides:
            pair = self.pairs[i // 2]
            leg: RfqLeg = "buy_native" if i % 2 == 0 else "sell_native"
        else:
            pair = self.pairs[i]
            leg = "buy_native"
        return pair, leg

    def poll_one(
        self, pair: Pair, leg: RfqLeg = "buy_native", *, gap: bool = False
    ) -> FluxionRfqQuoteTick:
        if leg == "buy_native":
            token_in = pair.fluxion.quote_token_address
            token_out = pair.fluxion.native_token
            amount = self.amount_usdc_raw
        else:
            token_in = pair.fluxion.native_token
            token_out = pair.fluxion.quote_token_address
            amount = self.amount_native_raw
        payload = {
            "tokenIn": token_in,
            "tokenOut": token_out,
            "amount": amount,
            "type": self.rfq.request_type,
        }
        urls = [str(self.rfq.quote_url), str(self.rfq.proxy_quote_url)]
        if not self.prefer_primary_url:
            urls = list(reversed(urls))

        poll_ts = now_ms()
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
                    "rfq quote HTTP %s from %s for %s/%s",
                    r.status_code,
                    url,
                    pair.id,
                    leg,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "rfq quote transport error %s for %s/%s: %s", url, pair.id, leg, exc
                )
                last_status = 0
        recv = now_ms()
        return parse_rfq_response(
            pair_id=pair.id,
            token_in=token_in,
            token_out=token_out,
            amount_in=amount,
            poll_ts_ms=poll_ts,
            recv_ts_ms=recv,
            http_status=last_status or 0,
            body=last_body if isinstance(last_body, dict) else None,
            gap=gap,
        )

    def poll_next(self, *, gap: bool = False) -> FluxionRfqQuoteTick:
        pair, leg = self.next_job()
        return self.poll_one(pair, leg, gap=gap)
