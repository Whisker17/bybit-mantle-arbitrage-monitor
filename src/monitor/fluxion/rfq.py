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

# HTTP outcomes that mean "the RFQ endpoint answered productively".
# 200 = executable quote; 204 = no resting quote (not an error).
RFQ_HTTP_REACHABLE = frozenset({200, 204})


def is_rfq_http_error(http_status: int) -> bool:
    """True when the status is a real HTTP failure (not 200/204, not transport 0)."""
    return http_status > 0 and http_status not in RFQ_HTTP_REACHABLE


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
    side_hint: str | None = None,
) -> FluxionRfqQuoteTick:
    """Map HTTP status + JSON body to a tick.

    * 200 + price → available=True
    * 204 → available=False (no resting quote; not an error)
    * other HTTP (e.g. 451) → available=False, price/amount null, status kept
      (WHI-974: failures must leave a journal row)
    """
    if body is None:
        # 204 no-quote, HTTP errors (451/…), transport 0, or empty 200 body.
        return FluxionRfqQuoteTick(
            pair_id=pair_id,
            poll_ts_ms=poll_ts_ms,
            recv_ts_ms=recv_ts_ms,
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            amount_out=None,
            price=None,
            side=side_hint,
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
    side_raw = body.get("side")
    side = side_hint if side_raw is None else str(side_raw)
    request_id = None if body.get("requestId") is None else str(body.get("requestId"))
    return FluxionRfqQuoteTick(
        pair_id=pair_id,
        poll_ts_ms=poll_ts_ms,
        recv_ts_ms=recv_ts_ms,
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        amount_out=None if amount_out is None else str(amount_out),
        price=price,
        side=side,
        request_id=request_id,
        http_status=http_status,
        available=http_status == 200 and price is not None,
        gap=gap,
    )


class RfqPoller:
    """Round-robin EXACT_INPUT quote polls respecting global rate limit.

    Each ``poll_next`` issues **one** HTTP schedule slot (one pair×leg). With
    ``poll_both_sides``, the schedule interleaves buy_native and sell_native
    (2N slots). Sleep between polls is ``60 / rate_limit_per_minute`` (1s at
    60/min). With N=11 and both sides, each pair×leg repeats every **22s**.

    URL failover: try primary then proxy. **Every definitive HTTP response is
    returned as a tick** (WHI-974) — intermediate 451s leave rows even when the
    proxy later returns 200. Transport failures try the next URL; if all fail,
    a status=0 tick is returned.
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
    ) -> list[FluxionRfqQuoteTick]:
        """Poll one pair×leg; return **all** ticks for this attempt (incl. errors).

        Intermediate non-200/204 responses are included so journal coverage /
        error rates are not silently inflated by proxy failover (WHI-974).
        """
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
        ticks: list[FluxionRfqQuoteTick] = []
        for url in urls:
            try:
                r = self._client.post(url, json=payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "rfq quote transport error %s for %s/%s: %s", url, pair.id, leg, exc
                )
                continue

            status = r.status_code
            body: dict[str, Any] | None = None
            if status == 200:
                try:
                    parsed = r.json()
                    body = parsed if isinstance(parsed, dict) else None
                except Exception:  # noqa: BLE001
                    body = None
            elif status == 204:
                body = None
            else:
                logger.warning(
                    "rfq quote HTTP %s from %s for %s/%s",
                    status,
                    url,
                    pair.id,
                    leg,
                )

            recv = now_ms()
            # side_hint only when we have a 200 body (vendor side may still
            # win). Errors, 204, and empty 200 keep side=NULL so
            # latest_rfq_quote's side filter does not blank the last good quote
            # (WHI-974 review).
            tick = parse_rfq_response(
                pair_id=pair.id,
                token_in=token_in,
                token_out=token_out,
                amount_in=amount,
                poll_ts_ms=poll_ts,
                recv_ts_ms=recv,
                http_status=status,
                body=body,
                gap=gap,
                side_hint=leg if (status == 200 and body is not None) else None,
            )
            ticks.append(tick)
            # Reachable product response ends the failover chain; errors try next URL.
            if status in RFQ_HTTP_REACHABLE:
                break

        if not ticks:
            # All URLs transport-failed.
            recv = now_ms()
            ticks.append(
                parse_rfq_response(
                    pair_id=pair.id,
                    token_in=token_in,
                    token_out=token_out,
                    amount_in=amount,
                    poll_ts_ms=poll_ts,
                    recv_ts_ms=recv,
                    http_status=0,
                    body=None,
                    gap=gap,
                    side_hint=None,
                )
            )
        return ticks

    def poll_next(self, *, gap: bool = False) -> list[FluxionRfqQuoteTick]:
        pair, leg = self.next_job()
        return self.poll_one(pair, leg, gap=gap)
