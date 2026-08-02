"""Pyth Hermes HTTP client — latest equity prices (WHI-778)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from monitor.quotes import UnderlyingPriceTick, now_ms
from monitor.underlying.config import UnderlyingConfig
from monitor.underlying.price_type import classify_price_type


def scale_pyth_price(price_raw: str | int, expo: int) -> Decimal:
    """``price * 10^expo`` as Decimal (Hermes price payload)."""
    return Decimal(str(price_raw)) * (Decimal(10) ** int(expo))


def parse_hermes_latest(
    body: dict[str, Any] | list[Any],
    *,
    cfg: UnderlyingConfig,
    tickers: set[str] | None = None,
    recv_ts_ms: int | None = None,
    now_ms_value: int | None = None,
    gap: bool = False,
) -> tuple[list[UnderlyingPriceTick], dict[str, Decimal]]:
    """Map Hermes latest JSON → ticks + raw feed prices by feed id.

    Returns ``(ticks, prices_by_feed_id)``. FX feeds are only in the map, not
    as equity ticks.
    """
    recv = recv_ts_ms if recv_ts_ms is not None else now_ms()
    now = now_ms_value if now_ms_value is not None else recv
    parsed = _extract_parsed(body)
    feed_to_ticker = cfg.feed_id_to_ticker()
    prices_by_feed: dict[str, Decimal] = {}
    ticks: list[UnderlyingPriceTick] = []

    for item in parsed:
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get("id") or "").lower().removeprefix("0x")
        price_obj = item.get("price")
        if not isinstance(price_obj, dict):
            continue
        try:
            px = scale_pyth_price(price_obj["price"], int(price_obj["expo"]))
            publish_time = int(price_obj["publish_time"])
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        prices_by_feed[raw_id] = px
        conf_raw = price_obj.get("conf")
        conf: Decimal | None = None
        if conf_raw is not None:
            try:
                conf = scale_pyth_price(conf_raw, int(price_obj["expo"]))
            except (TypeError, ValueError, InvalidOperation):
                conf = None

        ticker = feed_to_ticker.get(raw_id)
        if ticker is None:
            continue
        tcfg = cfg.tickers.get(ticker)
        if tcfg is None or tcfg.uncovered or tcfg.prefer_yahoo:
            continue
        if tickers is not None and ticker not in tickers:
            continue

        as_of_ms = publish_time * 1000
        price_type = classify_price_type(
            as_of_ms=as_of_ms,
            now_ms=now,
            session=cfg.session,
            stale_after_open_ms=cfg.stale_after_open_ms,
            stale_after_closed_ms=cfg.stale_after_closed_ms,
            stale_after_abs_ms=cfg.stale_after_abs_ms,
        )
        ticks.append(
            UnderlyingPriceTick(
                ticker=ticker,
                price=px,
                currency=tcfg.currency,
                price_type=price_type,
                as_of_ms=as_of_ms,
                recv_ts_ms=recv,
                source="pyth_hermes",
                feed_id=raw_id,
                conf=conf,
                gap=gap,
            )
        )
    return ticks, prices_by_feed


def _extract_parsed(body: dict[str, Any] | list[Any]) -> list[Any]:
    if isinstance(body, list):
        return body
    parsed = body.get("parsed")
    if isinstance(parsed, list):
        return parsed
    return []


def hermes_has_equity_feed(
    feeds: list[Any] | None,
    *,
    ticker: str,
) -> bool:
    """True when Hermes ``price_feeds`` includes a usable equity for ``ticker``.

    Prefers exact RTH ``Equity.US.{T}/USD``. Also accepts any non-extended-hours
    ``Equity.*`` symbol whose base equals the ticker (covers future KR/HK
    listings that would otherwise stay falsely ``uncovered``).
    """
    if not feeds:
        return False
    t = ticker.upper()
    want_us_rth = f"Equity.US.{t}/USD"
    for item in feeds:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes")
        if not isinstance(attrs, dict):
            continue
        symbol = str(attrs.get("symbol") or "")
        if not symbol.startswith("Equity."):
            continue
        # Ignore deprecated extended-hours suffixes.
        if symbol.endswith((".PRE", ".POST", ".ON")):
            continue
        if symbol == want_us_rth:
            return True
        # Equity.{CCY}.{BASE}/{QUOTE} — match base to ticker.
        try:
            rest = symbol.removeprefix("Equity.")
            base_quote = rest.split(".", 1)[1]  # after region
            base = base_quote.split("/", 1)[0]
        except (IndexError, ValueError):
            continue
        if base.upper() == t:
            return True
    return False


class HermesClient:
    """Thin HTTP wrapper around Hermes latest price updates."""

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
            headers={"User-Agent": "bybit-mantle-arbitrage-monitor/underlying"},
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_latest(self, feed_ids: list[str]) -> dict[str, Any]:
        if not feed_ids:
            return {"parsed": []}
        # Hermes accepts repeated ids[] query params.
        params: list[tuple[str, str | int | float | bool | None]] = [
            ("ids[]", fid) for fid in feed_ids
        ]
        url = f"{self.base_url}/v2/updates/price/latest"
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError(f"Hermes latest: expected object, got {type(data).__name__}")
        return data

    def search_price_feeds(self, query: str) -> list[Any]:
        """Hermes ``/v2/price_feeds?query=…`` — used by uncovered coverage probe."""
        url = f"{self.base_url}/v2/price_feeds"
        resp = self._client.get(url, params={"query": query})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise ValueError(
                f"Hermes price_feeds: expected list, got {type(data).__name__}"
            )
        return data
