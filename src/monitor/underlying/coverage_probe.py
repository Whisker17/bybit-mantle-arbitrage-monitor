"""Uncovered-ticker coverage guardrail (WHI-787).

A one-time human "private / no feed" flag must not freeze forever. Periodically
probe Pyth Hermes + Yahoo for every ``uncovered: true`` ticker; if a public
source actually has a price, emit a mismatch for WARN logs and ``/api/health``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from monitor.underlying.config import UnderlyingConfig
from monitor.underlying.pyth import HermesClient, hermes_has_equity_feed
from monitor.underlying.yahoo import YahooChartClient, yahoo_chart_has_price

logger = logging.getLogger(__name__)

# Journal meta keys (collector → health). Keep stable for ops/scripts.
META_MISMATCHES = "underlying_uncovered_mismatches"
META_PROBE_MS = "underlying_uncovered_probe_ms"
META_PROBE_ERRORS = "underlying_uncovered_probe_errors"


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

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> UncoveredMismatch | None:
        ticker = raw.get("ticker")
        sources = raw.get("sources")
        detail = raw.get("detail")
        if not isinstance(ticker, str) or not ticker:
            return None
        if not isinstance(sources, list):
            return None
        src_tuple = tuple(str(s) for s in sources if s)
        if not src_tuple:
            return None
        return cls(
            ticker=ticker,
            sources=src_tuple,
            detail=str(detail) if detail is not None else "",
        )


@dataclass(frozen=True, slots=True)
class ProbeError:
    """Per-ticker source failure during an uncovered probe."""

    ticker: str
    source: str
    error: str

    def to_dict(self) -> dict[str, Any]:
        return {"ticker": self.ticker, "source": self.source, "error": self.error}


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """Result of one uncovered-coverage probe pass."""

    mismatches: list[UncoveredMismatch] = field(default_factory=list)
    errors: list[ProbeError] = field(default_factory=list)

    @property
    def inconclusive(self) -> bool:
        """True when every uncovered ticker failed both sources (no verdict)."""
        return bool(self.errors) and not self.mismatches


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
    if hermes_error is None and hermes_has_equity_feed(hermes_feeds, ticker=ticker):
        sources.append("pyth_hermes")
        bits.append(f"Hermes lists an Equity feed for {ticker.upper()}")
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
        if not isinstance(item, dict):
            continue
        parsed = UncoveredMismatch.from_dict(item)
        if parsed is not None:
            out.append(parsed.to_dict())
    return out


def errors_to_meta_json(errors: list[ProbeError]) -> str:
    return json.dumps([e.to_dict() for e in errors], separators=(",", ":"))


def errors_from_meta_json(raw: str | None) -> list[dict[str, Any]]:
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
        if (
            isinstance(item, dict)
            and isinstance(item.get("ticker"), str)
            and isinstance(item.get("source"), str)
        ):
            out.append(
                {
                    "ticker": item["ticker"],
                    "source": item["source"],
                    "error": str(item.get("error") or ""),
                }
            )
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

    def probe_once(self) -> ProbeOutcome:
        """Probe every uncovered ticker.

        Returns mismatches (config says uncovered, source has data) and
        per-source errors so a total outage is not stamped as "all clear".
        Empty uncovered list → empty outcome (no network).
        """
        names = self.cfg.uncovered_tickers()
        if not names:
            return ProbeOutcome()
        found: list[UncoveredMismatch] = []
        errors: list[ProbeError] = []
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
                errors.append(
                    ProbeError(ticker=name, source="yahoo", error=yahoo_err)
                )
                logger.debug("uncovered probe yahoo failed ticker=%s: %s", name, exc)

            hermes_feeds: list[Any] | None = None
            hermes_err: str | None = None
            try:
                hermes_feeds = self._hermes.search_price_feeds(name)
            except Exception as exc:  # noqa: BLE001
                hermes_err = str(exc)
                errors.append(
                    ProbeError(ticker=name, source="pyth_hermes", error=hermes_err)
                )
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
        if errors and not found:
            logger.warning(
                "uncovered coverage probe inconclusive: %d source error(s), "
                "0 mismatches (cannot confirm still uncovered)",
                len(errors),
            )
        return ProbeOutcome(mismatches=found, errors=errors)
