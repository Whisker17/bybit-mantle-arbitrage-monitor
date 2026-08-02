"""Underlying coverage guardrails (WHI-787 + WHI-794).

1. **Uncovered reverse (WHI-787):** a one-time human ``uncovered: true`` flag
   must not freeze forever. Periodically probe Pyth Hermes + Yahoo; if a public
   source actually has a price, emit a mismatch for WARN + ``/api/health``.

2. **Unpublished Pyth feed (WHI-794 reverse):** config pins a Hermes ``feed_id``
   but latest returns ``price=0`` / ``publish_time=0`` (registered, never
   published). WARN + health so Yahoo gap-fill is configured before zeros
   reappear on the panel.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from monitor.underlying.config import UnderlyingConfig
from monitor.underlying.pyth import (
    HermesClient,
    hermes_has_equity_feed,
    hermes_quote_is_valid,
    iter_hermes_quotes,
)
from monitor.underlying.yahoo import YahooChartClient, yahoo_chart_has_price

logger = logging.getLogger(__name__)

# Journal meta keys (collector → health). Keep stable for ops/scripts.
META_MISMATCHES = "underlying_uncovered_mismatches"
META_PROBE_MS = "underlying_uncovered_probe_ms"
META_PROBE_ERRORS = "underlying_uncovered_probe_errors"
# WHI-794: configured Hermes feeds that never published.
META_UNPUBLISHED = "underlying_unpublished_feeds"


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

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ProbeError | None:
        ticker = raw.get("ticker")
        source = raw.get("source")
        if not isinstance(ticker, str) or not ticker:
            return None
        if not isinstance(source, str) or not source:
            return None
        return cls(
            ticker=ticker,
            source=source,
            error=str(raw.get("error") or ""),
        )


@dataclass(frozen=True, slots=True)
class UnpublishedFeed:
    """Config pins a Hermes feed_id, but latest has never published (WHI-794)."""

    ticker: str
    feed_id: str
    publish_time: int
    price: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "feed_id": self.feed_id,
            "publish_time": self.publish_time,
            "price": self.price,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> UnpublishedFeed | None:
        ticker = raw.get("ticker")
        feed_id = raw.get("feed_id")
        if not isinstance(ticker, str) or not ticker:
            return None
        if not isinstance(feed_id, str) or not feed_id:
            return None
        try:
            publish_time = int(raw.get("publish_time", 0))
        except (TypeError, ValueError):
            publish_time = 0
        return cls(
            ticker=ticker,
            feed_id=feed_id,
            publish_time=publish_time,
            price=str(raw.get("price") if raw.get("price") is not None else "0"),
            detail=str(raw.get("detail") or ""),
        )


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """Result of one coverage probe pass (uncovered + unpublished feeds)."""

    mismatches: list[UncoveredMismatch] = field(default_factory=list)
    errors: list[ProbeError] = field(default_factory=list)
    unpublished_feeds: list[UnpublishedFeed] = field(default_factory=list)
    # False only when Hermes latest for unpublished check raised (do not clear
    # prior META_UNPUBLISHED on stamp).
    hermes_latest_ok: bool = True

    @property
    def inconclusive(self) -> bool:
        """True when errors exist and neither reverse hit was confirmed.

        Covers total Yahoo/Hermes outage on uncovered probes, and Hermes-latest
        failures when checking unpublished pins (no mismatches and no
        unpublished rows).
        """
        return (
            bool(self.errors)
            and not self.mismatches
            and not self.unpublished_feeds
        )


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
        if not isinstance(item, dict):
            continue
        parsed = ProbeError.from_dict(item)
        if parsed is not None:
            out.append(parsed.to_dict())
    return out


def unpublished_to_meta_json(rows: list[UnpublishedFeed]) -> str:
    return json.dumps([r.to_dict() for r in rows], separators=(",", ":"))


def unpublished_from_meta_json(raw: str | None) -> list[dict[str, Any]]:
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
        parsed = UnpublishedFeed.from_dict(item)
        if parsed is not None:
            out.append(parsed.to_dict())
    return out


def detect_unpublished_pyth_feeds(
    body: dict[str, Any] | list[Any],
    *,
    cfg: UnderlyingConfig,
    tickers: set[str] | None = None,
) -> list[UnpublishedFeed]:
    """Pure: Hermes latest rows with ``publish_time==0`` or ``price<=0``.

    Only considers config tickers that still pin a ``feed_id`` (not
    ``prefer_yahoo`` / uncovered). Missing ids in the response are ignored —
    those are transport/chunk issues, not "never published".

    ``price`` is a decimal string for journal meta / JSON wire (not used in
    arithmetic on the probe path).
    """
    feed_to_ticker = cfg.feed_id_to_ticker()
    if not feed_to_ticker:
        return []

    found: list[UnpublishedFeed] = []
    seen: set[str] = set()
    for q in iter_hermes_quotes(body):
        ticker = feed_to_ticker.get(q.feed_id)
        if ticker is None or ticker in seen:
            continue
        if tickers is not None and ticker not in tickers:
            continue
        if hermes_quote_is_valid(price=q.price, publish_time=q.publish_time):
            continue
        seen.add(ticker)
        tcfg = cfg.tickers.get(ticker)
        if tcfg is not None and tcfg.yahoo_symbol:
            detail = (
                f"Hermes feed {q.feed_id[:12]}… still never published "
                f"(price={q.price} publish_time={q.publish_time}); "
                f"Yahoo gap-fill via {tcfg.yahoo_symbol} is configured"
            )
        else:
            detail = (
                f"Hermes feed {q.feed_id[:12]}… returned price={q.price} "
                f"publish_time={q.publish_time} (registered but never "
                "published) — add yahoo_symbol gap-fill in "
                "config/underlying.yaml"
            )
        found.append(
            UnpublishedFeed(
                ticker=ticker,
                feed_id=q.feed_id,
                publish_time=q.publish_time,
                price=str(q.price),
                detail=detail,
            )
        )
    found.sort(key=lambda r: r.ticker)
    return found


class UncoveredCoverageProbe:
    """Live probe: uncovered reverse (WHI-787) + unpublished Pyth feeds (WHI-794)."""

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
        """Probe uncovered tickers + configured Hermes feed validity.

        Returns mismatches (config says uncovered, source has data),
        unpublished Pyth feeds (config pins feed_id, latest never published),
        and per-source errors so a total outage is not stamped as "all clear".
        """
        found: list[UncoveredMismatch] = []
        errors: list[ProbeError] = []
        unpublished: list[UnpublishedFeed] = []

        names = self.cfg.uncovered_tickers()
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
        if errors and not found and names:
            logger.warning(
                "uncovered coverage probe inconclusive: %d source error(s), "
                "0 mismatches (cannot confirm still uncovered)",
                len(errors),
            )

        # WHI-794 reverse: feed_id configured but Hermes never published.
        feed_ids = self.cfg.pyth_feed_ids()
        hermes_latest_ok = True
        if feed_ids:
            try:
                body = self._hermes.fetch_latest(feed_ids)
                unpublished = detect_unpublished_pyth_feeds(body, cfg=self.cfg)
                for row in unpublished:
                    tcfg = self.cfg.tickers.get(row.ticker)
                    if tcfg is not None and tcfg.yahoo_symbol:
                        # Gap-fill already wired — keep advisory soft (debug).
                        logger.debug(
                            "pyth feed never published (yahoo gap-fill ok) "
                            "ticker=%s feed=%s…",
                            row.ticker,
                            row.feed_id[:12],
                        )
                    else:
                        logger.warning(
                            "pyth feed never published ticker=%s feed=%s…: %s",
                            row.ticker,
                            row.feed_id[:12],
                            row.detail,
                        )
            except Exception as exc:  # noqa: BLE001
                hermes_latest_ok = False
                errors.append(
                    ProbeError(
                        ticker="*",
                        source="pyth_hermes_latest",
                        error=str(exc)[:400],
                    )
                )
                logger.debug("unpublished feed probe hermes latest failed: %s", exc)

        return ProbeOutcome(
            mismatches=found,
            errors=errors,
            unpublished_feeds=unpublished,
            hermes_latest_ok=hermes_latest_ok if feed_ids else True,
        )
