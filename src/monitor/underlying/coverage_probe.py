"""Uncovered-ticker coverage guardrail (WHI-787).

A one-time human "private / no feed" flag must not freeze forever. Periodically
probe Pyth Hermes + Yahoo for every ``uncovered: true`` ticker; if a public
source actually has a price, emit a mismatch for WARN logs and ``/api/health``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from monitor.underlying.config import UnderlyingConfig
from monitor.underlying.pyth import HermesClient
from monitor.underlying.yahoo import YahooChartClient

logger = logging.getLogger(__name__)

# Journal meta keys (collector → health). Keep stable for ops/scripts.
META_MISMATCHES = "underlying_uncovered_mismatches"
META_PROBE_MS = "underlying_uncovered_probe_ms"


@dataclass(frozen=True, slots=True)
class UncoveredMismatch:
    """Config says uncovered, but a public source returned a usable quote."""

    ticker: str
    sources: tuple[str, ...]
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "sources": list(self.sources),
            "detail": self.detail,
        }


def yahoo_chart_has_price(body: dict[str, Any] | None) -> bool:
    """True when Yahoo chart JSON has a positive regularMarketPrice/previousClose."""
    if not body:
        return False
    try:
        meta = body["chart"]["result"][0]["meta"]
    except (KeyError, IndexError, TypeError):
        return False
    price = meta.get("regularMarketPrice")
    if price is None:
        price = meta.get("previousClose")
    if price is None:
        return False
    try:
        return float(price) > 0
    except (TypeError, ValueError):
        return False


def hermes_has_us_equity_feed(
    feeds: list[Any] | None,
    *,
    ticker: str,
) -> bool:
    """True when Hermes price_feeds includes ``Equity.US.{TICKER}/USD`` (RTH)."""
    if not feeds:
        return False
    want = f"Equity.US.{ticker.upper()}/USD"
    for item in feeds:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes")
        if not isinstance(attrs, dict):
            continue
        symbol = str(attrs.get("symbol") or "")
        # Exact RTH equity; ignore .PRE / .POST / .ON variants for "has coverage".
        if symbol == want:
            return True
    return False


def evaluate_uncovered_probe(
    ticker: str,
    *,
    yahoo_chart: dict[str, Any] | None = None,
    yahoo_error: str | None = None,
    hermes_feeds: list[Any] | None = None,
    hermes_error: str | None = None,
) -> UncoveredMismatch | None:
    """Pure: decide whether an uncovered ticker actually has public coverage.

    Network errors do **not** create mismatches (absence of evidence ≠ coverage).
    """
    sources: list[str] = []
    bits: list[str] = []
    if yahoo_error is None and yahoo_chart_has_price(yahoo_chart):
        sources.append("yahoo")
        bits.append("Yahoo chart returned a positive last price")
    if hermes_error is None and hermes_has_us_equity_feed(hermes_feeds, ticker=ticker):
        sources.append("pyth_hermes")
        bits.append(f"Hermes lists Equity.US.{ticker.upper()}/USD")
    if not sources:
        return None
    return UncoveredMismatch(
        ticker=ticker,
        sources=tuple(sources),
        detail="; ".join(bits),
    )


def mismatches_to_meta_json(mismatches: list[UncoveredMismatch]) -> str:
    """Serialize mismatches for journal meta (stable JSON list)."""
    return json.dumps([m.to_dict() for m in mismatches], separators=(",", ":"))


def mismatches_from_meta_json(raw: str | None) -> list[dict[str, Any]]:
    """Parse journal meta back to a list of dicts for health/API."""
    if raw is None or raw == "":
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict) and "ticker" in item:
            out.append(item)
    return out


class UncoveredCoverageProbe:
    """Live probe of all config ``uncovered`` tickers via Hermes + Yahoo."""

    def __init__(
        self,
        cfg: UnderlyingConfig,
        *,
        hermes: HermesClient | None = None,
        yahoo: YahooChartClient | None = None,
    ) -> None:
        self.cfg = cfg
        self._hermes = hermes or HermesClient(
            base_url=cfg.hermes_base_url,
            timeout_s=cfg.http_timeout_s,
        )
        self._owns_hermes = hermes is None
        # Yahoo client even when yahoo_fallback is false — probe is independent
        # of the collection path (ops still want to know a public tape exists).
        self._yahoo = yahoo or YahooChartClient(
            base_url=cfg.yahoo_chart_base_url,
            timeout_s=cfg.http_timeout_s,
        )
        self._owns_yahoo = yahoo is None

    def close(self) -> None:
        if self._owns_hermes:
            self._hermes.close()
        if self._owns_yahoo:
            self._yahoo.close()

    def probe_once(self) -> list[UncoveredMismatch]:
        """Probe every uncovered ticker; empty list when none or all still dark."""
        names = self.cfg.uncovered_tickers()
        if not names:
            return []
        found: list[UncoveredMismatch] = []
        for name in names:
            tcfg = self.cfg.tickers[name]
            # Prefer explicit yahoo_symbol if set; else try the canonical ticker.
            yahoo_sym = tcfg.yahoo_symbol or name
            yahoo_body: dict[str, Any] | None = None
            yahoo_err: str | None = None
            try:
                yahoo_body = self._yahoo.fetch_chart(yahoo_sym)
            except Exception as exc:  # noqa: BLE001
                yahoo_err = str(exc)
                logger.debug("uncovered probe yahoo failed ticker=%s: %s", name, exc)

            hermes_feeds: list[Any] | None = None
            hermes_err: str | None = None
            try:
                hermes_feeds = self._hermes.search_price_feeds(name)
            except Exception as exc:  # noqa: BLE001
                hermes_err = str(exc)
                logger.debug("uncovered probe hermes failed ticker=%s: %s", name, exc)

            hit = evaluate_uncovered_probe(
                name,
                yahoo_chart=yahoo_body,
                yahoo_error=yahoo_err,
                hermes_feeds=hermes_feeds,
                hermes_error=hermes_err,
            )
            if hit is not None:
                found.append(hit)
                logger.warning(
                    "uncovered ticker %s has public coverage (%s): %s — "
                    "remove uncovered flag and wire a feed in config/underlying.yaml",
                    hit.ticker,
                    ",".join(hit.sources),
                    hit.detail,
                )
        return found
