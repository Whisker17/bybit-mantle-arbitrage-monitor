"""Poll Hermes (+ optional Yahoo) and emit UnderlyingPriceTick rows."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from monitor.metrics.session import SessionKind, session_kind
from monitor.quotes import UnderlyingPriceTick, now_ms
from monitor.underlying.config import UnderlyingConfig
from monitor.underlying.pyth import HermesClient, parse_hermes_latest
from monitor.underlying.yahoo import YahooChartClient, parse_yahoo_chart

logger = logging.getLogger(__name__)

OnTicks = Callable[[list[UnderlyingPriceTick]], None]


class UnderlyingPoller:
    """One batch poll of configured underlyings for a market ticker set."""

    def __init__(
        self,
        cfg: UnderlyingConfig,
        *,
        tickers: list[str] | None = None,
        hermes: HermesClient | None = None,
        yahoo: YahooChartClient | None = None,
    ) -> None:
        self.cfg = cfg
        if tickers is None:
            self.tickers = list(cfg.tickers.keys())
        else:
            self.tickers = [t for t in tickers if t in cfg.tickers]
        self._hermes = hermes or HermesClient(
            base_url=cfg.hermes_base_url,
            timeout_s=cfg.http_timeout_s,
        )
        self._owns_hermes = hermes is None
        self._yahoo: YahooChartClient | None
        if cfg.yahoo_fallback:
            self._yahoo = yahoo or YahooChartClient(
                base_url=cfg.yahoo_chart_base_url,
                timeout_s=cfg.http_timeout_s,
            )
            self._owns_yahoo = yahoo is None
        else:
            self._yahoo = None
            self._owns_yahoo = False
        self._gap = False

    def close(self) -> None:
        if self._owns_hermes:
            self._hermes.close()
        if self._owns_yahoo and self._yahoo is not None:
            self._yahoo.close()

    def poll_interval_s(self, *, now_ms_value: int | None = None) -> float:
        ts = now_ms_value if now_ms_value is not None else now_ms()
        try:
            kind = session_kind(
                datetime.fromtimestamp(ts / 1000, tz=UTC),
                config=self.cfg.session,
            )
        except ValueError:
            return self.cfg.closed_poll_interval_s
        if kind is SessionKind.OPEN:
            return self.cfg.open_poll_interval_s
        return self.cfg.closed_poll_interval_s

    def poll_once(
        self,
        *,
        gap: bool | None = None,
        now_ms_value: int | None = None,
    ) -> list[UnderlyingPriceTick]:
        """Fetch all covered tickers once. Uncovered tickers are skipped."""
        use_gap = self._gap if gap is None else gap
        recv = now_ms()
        now = now_ms_value if now_ms_value is not None else recv
        want = set(self.tickers)
        ticks: list[UnderlyingPriceTick] = []
        prices_by_feed: dict[str, Decimal] = {}

        feed_ids = self.cfg.pyth_feed_ids(want, include_fx=self.cfg.needs_fx(want))

        if feed_ids:
            try:
                body = self._hermes.fetch_latest(feed_ids)
                hermes_ticks, prices_by_feed = parse_hermes_latest(
                    body,
                    cfg=self.cfg,
                    tickers=want,
                    recv_ts_ms=recv,
                    now_ms_value=now,
                    gap=use_gap,
                )
                ticks.extend(hermes_ticks)
                self._gap = False
            except Exception as exc:  # noqa: BLE001
                logger.warning("hermes poll failed: %s", exc)
                self._gap = True
                use_gap = True

        usd_krw = self._fx_usd_krw(prices_by_feed)
        for name in self.tickers:
            tcfg = self.cfg.tickers.get(name)
            if tcfg is None or tcfg.uncovered:
                continue
            if not tcfg.prefer_yahoo:
                continue
            if not self.cfg.yahoo_fallback or self._yahoo is None:
                logger.debug("yahoo fallback disabled; skip %s", name)
                continue
            if not tcfg.yahoo_symbol:
                continue
            try:
                chart = self._yahoo.fetch_chart(tcfg.yahoo_symbol)
                tick = parse_yahoo_chart(
                    chart,
                    ticker=name,
                    currency=tcfg.currency,
                    cfg=self.cfg,
                    recv_ts_ms=recv,
                    now_ms_value=now,
                    usd_krw=usd_krw,
                    gap=use_gap,
                )
                if tick is not None:
                    ticks.append(tick)
                    self._gap = False
                else:
                    logger.warning("yahoo chart empty for %s", name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("yahoo poll failed ticker=%s: %s", name, exc)
                self._gap = True

        return ticks

    def _fx_usd_krw(self, prices_by_feed: dict[str, Decimal]) -> Decimal | None:
        fid = self.cfg.fx_usd_krw_feed_id
        if not fid:
            return None
        key = fid.lower().removeprefix("0x")
        return prices_by_feed.get(key)
