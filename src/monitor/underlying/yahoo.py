"""Optional Yahoo chart fallback for Pyth gaps (SKHY US ADR) — WHI-778/785.

Unofficial endpoint; see docs/references/underlying-price-source.md license note.
When Yahoo meta currency is already USD, writes source ``yahoo`` with no FX.
KRW quotes still convert via optional Pyth ``FX.USD/KRW`` (``yahoo+pyth_fx``).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from monitor.quotes import UnderlyingPriceTick, now_ms
from monitor.underlying.config import UnderlyingConfig
from monitor.underlying.price_type import PriceType, classify_price_type


def market_state_hint(state: str | None) -> PriceType | None:
    if not state:
        return None
    s = state.upper()
    if s in {"PRE", "PREPRE"}:
        return "pre"
    if s in {"POST", "POSTPOST"}:
        return "post"
    if s == "REGULAR":
        return "live"
    if s == "CLOSED":
        return "close"
    return None


def yahoo_chart_meta(body: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return Yahoo chart ``meta`` object, or None when the payload is unusable."""
    if not body:
        return None
    try:
        meta = body["chart"]["result"][0]["meta"]
    except (KeyError, IndexError, TypeError):
        return None
    return meta if isinstance(meta, dict) else None


def yahoo_meta_last_price(meta: dict[str, Any] | None) -> Decimal | None:
    """Positive last price from Yahoo meta (regularMarketPrice, else previousClose)."""
    if not meta:
        return None
    price_raw = meta.get("regularMarketPrice")
    if price_raw is None:
        price_raw = meta.get("previousClose")
    if price_raw is None:
        return None
    try:
        price = Decimal(str(price_raw))
    except (InvalidOperation, ValueError):
        return None
    if price <= 0:
        return None
    return price


def yahoo_chart_has_price(body: dict[str, Any] | None) -> bool:
    """True when Yahoo chart JSON has a positive last/previous price (WHI-787)."""
    return yahoo_meta_last_price(yahoo_chart_meta(body)) is not None


def parse_yahoo_chart(
    body: dict[str, Any],
    *,
    ticker: str,
    currency: str,
    cfg: UnderlyingConfig,
    recv_ts_ms: int | None = None,
    now_ms_value: int | None = None,
    usd_krw: Decimal | None = None,
    gap: bool = False,
) -> UnderlyingPriceTick | None:
    """Extract last price from Yahoo chart meta; optional KRW→USD conversion."""
    recv = recv_ts_ms if recv_ts_ms is not None else now_ms()
    now = now_ms_value if now_ms_value is not None else recv
    meta = yahoo_chart_meta(body)
    if meta is None:
        return None

    price = yahoo_meta_last_price(meta)
    if price is None:
        return None

    as_of_s = meta.get("regularMarketTime")
    if as_of_s is None:
        as_of_s = meta.get("currentTradingPeriod", {}).get("regular", {}).get("end")
    if as_of_s is None:
        return None
    try:
        as_of_ms = int(as_of_s) * 1000
    except (TypeError, ValueError):
        return None

    raw_ccy = str(meta.get("currency") or currency).upper()
    source = "yahoo"
    out_ccy = raw_ccy
    if raw_ccy == "KRW" and usd_krw is not None and usd_krw > 0:
        price = price / usd_krw
        out_ccy = "USD"
        source = "yahoo+pyth_fx"

    hint = market_state_hint(
        None if meta.get("marketState") is None else str(meta.get("marketState"))
    )
    price_type = classify_price_type(
        as_of_ms=as_of_ms,
        now_ms=now,
        session=cfg.session,
        stale_after_open_ms=cfg.stale_after_open_ms,
        stale_after_closed_ms=cfg.stale_after_closed_ms,
        stale_after_abs_ms=cfg.stale_after_abs_ms,
        source_session_hint=hint,
    )
    return UnderlyingPriceTick(
        ticker=ticker,
        price=price,
        currency=out_ccy,
        price_type=price_type,
        as_of_ms=as_of_ms,
        recv_ts_ms=recv,
        source=source,
        feed_id=None,
        conf=None,
        gap=gap,
    )


class YahooChartClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout_s,
            headers={"User-Agent": "Mozilla/5.0 (compatible; monitor-underlying/1.0)"},
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_chart(self, symbol: str) -> dict[str, Any]:
        url = f"{self.base_url}/v8/finance/chart/{symbol}"
        resp = self._client.get(url, params={"interval": "1m", "range": "1d"})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError(f"Yahoo chart: expected object, got {type(data).__name__}")
        return data
