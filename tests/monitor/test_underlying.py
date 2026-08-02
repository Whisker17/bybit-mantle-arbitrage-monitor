"""Seams: pair→ticker map, price_type classify, Hermes parse, store insert (WHI-778)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from textwrap import dedent

import pytest

from monitor.metrics.config import SessionConfig
from monitor.quotes import UnderlyingPriceTick
from monitor.storage import SqliteStore
from monitor.storage.schema import SCHEMA_VERSION
from monitor.underlying.config import (
    UnderlyingConfigError,
    load_underlying_config,
)
from monitor.underlying.coverage_probe import (
    UncoveredCoverageProbe,
    evaluate_uncovered_probe,
    mismatches_from_meta_json,
    mismatches_to_meta_json,
)
from monitor.underlying.price_type import classify_price_type
from monitor.underlying.pyth import (
    hermes_has_equity_feed,
    parse_hermes_latest,
    scale_pyth_price,
)
from monitor.underlying.tickers import (
    pair_id_to_underlying_ticker,
    underlying_tickers_for_pairs,
)
from monitor.underlying.yahoo import (
    market_state_hint,
    parse_yahoo_chart,
    yahoo_chart_has_price,
)


def _session() -> SessionConfig:
    return SessionConfig(
        timezone="America/New_York",
        open="09:30",
        close="16:00",
        early_close="13:00",
    )


def test_pair_id_to_underlying_ticker() -> None:
    assert pair_id_to_underlying_ticker("AAPLx") == "AAPL"
    assert pair_id_to_underlying_ticker("AAPLB") == "AAPL"
    assert pair_id_to_underlying_ticker("TSLAB") == "TSLA"
    assert pair_id_to_underlying_ticker("MUB") == "MU"
    assert pair_id_to_underlying_ticker("SKHYB") == "SKHY"
    assert pair_id_to_underlying_ticker("SPCXx") == "SPCX"
    assert pair_id_to_underlying_ticker("SPCXB") == "SPCX"
    assert underlying_tickers_for_pairs(["AAPLx", "AAPLB", "TSLAx"]) == [
        "AAPL",
        "TSLA",
    ]


def test_load_checked_in_underlying_config() -> None:
    cfg = load_underlying_config()
    assert cfg.version == 1
    assert "AAPL" in cfg.tickers
    assert cfg.tickers["AAPL"].feed_id is not None
    # WHI-787: SPCX is Nasdaq-listed; Yahoo gap-fill (not uncovered/private).
    spcx = cfg.tickers["SPCX"]
    assert spcx.uncovered is False
    assert spcx.prefer_yahoo is True
    assert spcx.yahoo_symbol == "SPCX"
    assert spcx.feed_id is None
    assert spcx.pyth_symbol is None
    # WHI-785: SKHY is Nasdaq ADR (USD Yahoo), not KRX 000660.KS.
    skhy = cfg.tickers["SKHY"]
    assert skhy.prefer_yahoo is True
    assert skhy.yahoo_symbol == "SKHY"
    assert skhy.feed_id is None
    assert skhy.pyth_symbol is None
    assert cfg.fx_usd_krw_feed_id is None
    assert cfg.needs_fx({"SKHY"}) is False
    assert "AAPL" in cfg.covered_tickers()
    assert "SPCX" in cfg.covered_tickers()
    assert "SPCX" not in cfg.uncovered_tickers()
    assert cfg.uncovered_probe_interval_s > 0


def test_scale_pyth_price() -> None:
    assert scale_pyth_price("30985484", -5) == Decimal("309.85484")


def _ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
    """America/New_York wall clock → epoch ms (handles DST via zoneinfo)."""
    from zoneinfo import ZoneInfo

    dt = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York"))
    return int(dt.timestamp() * 1000)


def test_classify_live_during_rth() -> None:
    # 2026-07-31 Friday 10:00 ET — RTH open; as_of 1s earlier.
    now = _ms(2026, 7, 31, 10, 0)
    as_of = now - 1_000
    assert (
        classify_price_type(
            as_of_ms=as_of,
            now_ms=now,
            session=_session(),
            stale_after_open_ms=120_000,
            stale_after_closed_ms=432_000_000,
            stale_after_abs_ms=604_800_000,
        )
        == "live"
    )


def test_classify_close_on_weekend() -> None:
    # 2026-08-02 Sunday — closed; as_of = prior Friday 16:00 ET close.
    now = _ms(2026, 8, 2, 12, 0)
    as_of = _ms(2026, 7, 31, 16, 0)
    assert (
        classify_price_type(
            as_of_ms=as_of,
            now_ms=now,
            session=_session(),
            stale_after_open_ms=120_000,
            stale_after_closed_ms=432_000_000,
            stale_after_abs_ms=604_800_000,
        )
        == "close"
    )


def test_classify_source_live_hint_outside_nyse() -> None:
    """Honor source REGULAR→live even when our NYSE session is closed."""
    now = _ms(2026, 8, 2, 0, 30)  # Sunday evening ET
    as_of = now - 5_000
    assert (
        classify_price_type(
            as_of_ms=as_of,
            now_ms=now,
            session=_session(),
            stale_after_open_ms=120_000,
            stale_after_closed_ms=432_000_000,
            stale_after_abs_ms=604_800_000,
            source_session_hint="live",
        )
        == "live"
    )


def test_classify_close_not_post_on_weekday_after_rth() -> None:
    """Pyth freezes publish_time at RTH close — wall-clock post hours ≠ post print."""
    # Friday 17:30 ET (after 16:00 close); as_of = 16:00 close.
    now = _ms(2026, 7, 31, 17, 30)
    as_of = _ms(2026, 7, 31, 16, 0)
    assert (
        classify_price_type(
            as_of_ms=as_of,
            now_ms=now,
            session=_session(),
            stale_after_open_ms=120_000,
            stale_after_closed_ms=432_000_000,
            stale_after_abs_ms=604_800_000,
        )
        == "close"
    )
    # Explicit source hint still allows post.
    assert (
        classify_price_type(
            as_of_ms=as_of,
            now_ms=now,
            session=_session(),
            stale_after_open_ms=120_000,
            stale_after_closed_ms=432_000_000,
            stale_after_abs_ms=604_800_000,
            source_session_hint="post",
        )
        == "post"
    )


def test_classify_stale_dead_feed() -> None:
    now = _ms(2026, 8, 2, 12, 0)
    as_of = _ms(2025, 8, 29, 6, 30)  # ~1y old
    assert (
        classify_price_type(
            as_of_ms=as_of,
            now_ms=now,
            session=_session(),
            stale_after_open_ms=120_000,
            stale_after_closed_ms=432_000_000,
            stale_after_abs_ms=604_800_000,
        )
        == "stale"
    )


def test_classify_stale_during_open_if_old() -> None:
    now = _ms(2026, 7, 31, 10, 0)
    as_of = now - 300_000  # 5 min
    assert (
        classify_price_type(
            as_of_ms=as_of,
            now_ms=now,
            session=_session(),
            stale_after_open_ms=120_000,
            stale_after_closed_ms=432_000_000,
            stale_after_abs_ms=604_800_000,
        )
        == "stale"
    )


def test_parse_hermes_latest_maps_feed() -> None:
    cfg = load_underlying_config()
    aapl_id = cfg.tickers["AAPL"].feed_id
    assert aapl_id is not None
    # Friday close epoch used in classify_close_on_weekend.
    publish = int(_ms(2026, 7, 31, 16, 0) / 1000)
    body = {
        "parsed": [
            {
                "id": aapl_id,
                "price": {
                    "price": "30985484",
                    "conf": "84984",
                    "expo": -5,
                    "publish_time": publish,
                },
            }
        ]
    }
    now = _ms(2026, 8, 2, 12, 0)
    ticks, by_feed = parse_hermes_latest(
        body,
        cfg=cfg,
        tickers={"AAPL"},
        recv_ts_ms=now,
        now_ms_value=now,
    )
    assert len(ticks) == 1
    t = ticks[0]
    assert t.ticker == "AAPL"
    assert t.price == Decimal("309.85484")
    assert t.currency == "USD"
    assert t.price_type == "close"
    assert t.source == "pyth_hermes"
    assert t.as_of_ms == publish * 1000
    assert aapl_id.lower().removeprefix("0x") in by_feed


def test_parse_yahoo_chart_usd_no_fx() -> None:
    """WHI-785: Nasdaq ADR (SKHY) is already USD — no KRW FX conversion.

    Even if an FX rate is present (prefer_yahoo still batches FX.USD/KRW),
    USD meta must keep source=yahoo and the ADR price.
    """
    cfg = load_underlying_config()
    # Friday 16:00 ET close; as_of at close; now = Sunday → price_type close.
    as_of_s = int(_ms(2026, 7, 31, 16, 0) / 1000)
    body = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": 143.73,
                        "regularMarketTime": as_of_s,
                        "currency": "USD",
                        "marketState": "CLOSED",
                        "exchangeName": "NMS",
                    }
                }
            ]
        }
    }
    now = _ms(2026, 8, 2, 12, 0)
    tick = parse_yahoo_chart(
        body,
        ticker="SKHY",
        currency="USD",
        cfg=cfg,
        recv_ts_ms=now,
        now_ms_value=now,
        usd_krw=Decimal("1442.96"),  # must be ignored for USD meta
    )
    assert tick is not None
    assert tick.ticker == "SKHY"
    assert tick.currency == "USD"
    assert tick.source == "yahoo"
    assert tick.price == Decimal("143.73")
    assert tick.price_type == "close"


def test_parse_yahoo_chart_krw_to_usd() -> None:
    """Parser still converts KRW when caller supplies FX (no live KR ticker)."""
    cfg = load_underlying_config()
    as_of_s = int(_ms(2026, 7, 31, 16, 0) / 1000)
    body = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": 1_442_960.57,
                        "regularMarketTime": as_of_s,
                        "currency": "KRW",
                        "marketState": "CLOSED",
                    }
                }
            ]
        }
    }
    now = _ms(2026, 8, 2, 12, 0)
    tick = parse_yahoo_chart(
        body,
        ticker="SYNTH_KRW",  # not a config ticker; pure parser unit test
        currency="USD",
        cfg=cfg,
        recv_ts_ms=now,
        now_ms_value=now,
        usd_krw=Decimal("1442.96057"),
    )
    assert tick is not None
    assert tick.ticker == "SYNTH_KRW"
    assert tick.currency == "USD"
    assert tick.source == "yahoo+pyth_fx"
    assert tick.price == Decimal("1000")
    assert tick.price_type == "close"


def test_market_state_hint() -> None:
    assert market_state_hint("PRE") == "pre"
    assert market_state_hint("REGULAR") == "live"
    assert market_state_hint("CLOSED") == "close"


def test_insert_underlying_prices(tmp_path: Path) -> None:
    assert SCHEMA_VERSION >= 5
    store = SqliteStore(tmp_path / "u.db")
    tick = UnderlyingPriceTick(
        ticker="AAPL",
        price=Decimal("309.85"),
        currency="USD",
        price_type="close",
        as_of_ms=1_700_000_000_000,
        recv_ts_ms=1_700_000_000_050,
        source="pyth_hermes",
        feed_id="abc",
        conf=Decimal("0.1"),
        gap=False,
    )
    assert store.insert_underlying_prices([tick]) == 1
    assert store.count("underlying_prices") == 1
    # Same (ticker, as_of, source) is ignored (closed-session re-poll).
    assert store.insert_underlying_prices([tick]) == 1  # row attempted
    assert store.count("underlying_prices") == 1
    row = store._conn.execute(
        "SELECT ticker, price, price_type, source FROM underlying_prices"
    ).fetchone()
    assert row["ticker"] == "AAPL"
    assert row["price"] == "309.85"
    assert row["price_type"] == "close"
    assert row["source"] == "pyth_hermes"
    store.close()


def test_reader_latest_underlying_and_history(tmp_path: Path) -> None:
    """WHI-779: JournalReader surfaces underlying prints for premium join."""
    from monitor.storage import JournalReader

    db = tmp_path / "u2.db"
    store = SqliteStore(db)
    older = UnderlyingPriceTick(
        ticker="TSLA",
        price=Decimal("250"),
        currency="USD",
        price_type="close",
        as_of_ms=1_000,
        recv_ts_ms=1_010,
        source="pyth_hermes",
    )
    newer = UnderlyingPriceTick(
        ticker="TSLA",
        price=Decimal("255"),
        currency="USD",
        price_type="live",
        as_of_ms=2_000,
        recv_ts_ms=2_010,
        source="pyth_hermes",
    )
    store.insert_underlying_prices([older, newer])
    store.close()

    with JournalReader(db) as reader:
        latest = reader.latest_underlying_price("TSLA")
        assert latest is not None
        assert latest.price == Decimal("255")
        assert latest.price_type == "live"
        batch = reader.latest_underlying_prices(["TSLA", "AAPL"])
        assert "TSLA" in batch
        assert "AAPL" not in batch
        hist = reader.underlying_prices("TSLA", limit=10)
        assert [h.as_of_ms for h in hist] == [1_000, 2_000]



def test_invalid_underlying_config(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(
        dedent(
            """
            version: 1
            hermes_base_url: https://example
            open_poll_interval_s: 30
            closed_poll_interval_s: 300
            http_timeout_s: 20
            stale_after_open_ms: 1
            stale_after_abs_ms: 1
            stale_after_closed_ms: 1
            yahoo_fallback: false
            yahoo_chart_base_url: https://example
            uncovered_probe_interval_s: 3600
            session:
              timezone: America/New_York
              open: "09:30"
              close: "16:00"
              early_close: "13:00"
            tickers:
              FOO:
                currency: USD
                # neither feed nor yahoo
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(UnderlyingConfigError):
        load_underlying_config(p)


def test_bybit_inventory_underlying_tickers_nonempty() -> None:
    """WHI-788: bybit-fluxion inventory must map to covered US underlyings."""
    from monitor.symbols import load_pairs_config

    pairs = load_pairs_config()
    tickers = underlying_tickers_for_pairs([p.id for p in pairs.pairs])
    assert tickers, "bybit inventory produced zero underlying tickers"
    # Public US names the panel expects on overview Underlying / vs Und.
    for expected in ("AAPL", "NVDA", "TSLA", "AMZN", "META"):
        assert expected in tickers
    assert "SPCX" in tickers  # WHI-787: Yahoo gap-fill, not uncovered/private
    cfg = load_underlying_config()
    covered = [t for t in tickers if t in cfg.tickers and not cfg.tickers[t].uncovered]
    assert covered, "no covered tickers for bybit inventory"
    assert "SPCX" in covered
    assert cfg.tickers["SPCX"].prefer_yahoo is True


def test_binance_inventory_underlying_tickers_nonempty() -> None:
    """WHI-788: binance-pancake inventory also registers underlyings (dual-market)."""
    from monitor.symbols import load_bstocks_pairs_config

    bstocks = load_bstocks_pairs_config()
    tickers = underlying_tickers_for_pairs([p.id for p in bstocks.pairs])
    assert tickers
    for expected in ("AAPL", "NVDA", "TSLA"):
        assert expected in tickers


def test_daemon_underlying_tickers_both_markets(tmp_path: Path) -> None:
    """WHI-788: both market shapes resolve inventory tickers for the poller path."""
    from monitor.collector.config import load_collector_config
    from monitor.collector.daemon import CollectorDaemon
    from monitor.symbols import load_bstocks_pairs_config, load_pairs_config

    pairs = load_pairs_config()
    bybit_cfg = load_collector_config(market_id="bybit-fluxion")
    assert bybit_cfg.underlying_enabled is True
    assert bybit_cfg.is_bybit_fluxion
    store_b = SqliteStore(tmp_path / "bybit.db")
    bybit = CollectorDaemon(
        pairs, bybit_cfg, store_b, market_id="bybit-fluxion"
    )
    bybit_tickers = bybit._underlying_tickers_for_market()
    assert "AAPL" in bybit_tickers and "TSLA" in bybit_tickers
    store_b.close()

    bstocks = load_bstocks_pairs_config()
    pancake_cfg = load_collector_config(market_id="binance-pancake")
    assert pancake_cfg.underlying_enabled is True
    assert pancake_cfg.is_binance_pancake
    store_p = SqliteStore(tmp_path / "pancake.db")
    pancake = CollectorDaemon(
        None, pancake_cfg, store_p, bstocks=bstocks, market_id="binance-pancake"
    )
    pancake_tickers = pancake._underlying_tickers_for_market()
    assert "AAPL" in pancake_tickers and "TSLA" in pancake_tickers
    store_p.close()


# --- WHI-787 uncovered coverage guardrail ---


def test_yahoo_chart_has_price() -> None:
    assert yahoo_chart_has_price(None) is False
    assert yahoo_chart_has_price({"chart": {"result": []}}) is False
    assert (
        yahoo_chart_has_price(
            {"chart": {"result": [{"meta": {"regularMarketPrice": 108.37}}]}}
        )
        is True
    )
    assert (
        yahoo_chart_has_price(
            {"chart": {"result": [{"meta": {"previousClose": 115.0}}]}}
        )
        is True
    )
    assert (
        yahoo_chart_has_price(
            {"chart": {"result": [{"meta": {"regularMarketPrice": 0}}]}}
        )
        is False
    )


def test_hermes_has_equity_feed() -> None:
    feeds = [
        {"attributes": {"symbol": "Equity.US.AAPL/USD.PRE"}},
        {"attributes": {"symbol": "Equity.US.AAPL/USD"}},
        {"attributes": {"symbol": "Crypto.AAPLX/AAPL.RR"}},
    ]
    assert hermes_has_equity_feed(feeds, ticker="AAPL") is True
    assert hermes_has_equity_feed(feeds, ticker="SPCX") is False
    assert hermes_has_equity_feed([], ticker="AAPL") is False
    # PRE alone is not RTH coverage for the guardrail.
    assert (
        hermes_has_equity_feed(
            [{"attributes": {"symbol": "Equity.US.SPCX/USD.PRE"}}],
            ticker="SPCX",
        )
        is False
    )
    # Non-US equity base match (SKHY-class false-uncovered prevention).
    assert (
        hermes_has_equity_feed(
            [{"attributes": {"symbol": "Equity.KR.SKHY/KRW"}}],
            ticker="SKHY",
        )
        is True
    )


def test_evaluate_uncovered_probe_yahoo_hits() -> None:
    """Config uncovered but Yahoo has a print → mismatch (the SPCX bug class)."""
    chart = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": 108.37,
                        "currency": "USD",
                        "shortName": "Space Exploration Technologies",
                    }
                }
            ]
        }
    }
    hit = evaluate_uncovered_probe(
        "SPCX",
        yahoo_chart=chart,
        yahoo_error=None,
        hermes_feeds=[],
        hermes_error=None,
    )
    assert hit is not None
    assert hit.ticker == "SPCX"
    assert hit.sources == ("yahoo",)
    assert "Yahoo" in hit.detail


def test_evaluate_uncovered_probe_hermes_hits() -> None:
    feeds = [{"attributes": {"symbol": "Equity.US.FAKE/USD"}}]
    hit = evaluate_uncovered_probe(
        "FAKE",
        yahoo_chart=None,
        yahoo_error="timeout",
        hermes_feeds=feeds,
        hermes_error=None,
    )
    assert hit is not None
    assert "pyth_hermes" in hit.sources


def test_evaluate_uncovered_probe_no_false_positive_on_errors() -> None:
    """Network errors must not invent coverage."""
    assert (
        evaluate_uncovered_probe(
            "GHOST",
            yahoo_chart=None,
            yahoo_error="timeout",
            hermes_feeds=None,
            hermes_error="timeout",
        )
        is None
    )
    assert (
        evaluate_uncovered_probe(
            "GHOST",
            yahoo_chart={"chart": {"result": [{"meta": {}}]}},
            yahoo_error=None,
            hermes_feeds=[],
            hermes_error=None,
        )
        is None
    )


def test_mismatches_meta_roundtrip() -> None:
    from monitor.underlying.coverage_probe import UncoveredMismatch

    ms = [
        UncoveredMismatch(
            ticker="SPCX", sources=("yahoo",), detail="Yahoo has price"
        )
    ]
    raw = mismatches_to_meta_json(ms)
    back = mismatches_from_meta_json(raw)
    assert back == [
        {"ticker": "SPCX", "sources": ["yahoo"], "detail": "Yahoo has price"}
    ]
    assert mismatches_from_meta_json(None) == []
    assert mismatches_from_meta_json("not-json") == []


def test_uncovered_probe_skips_when_list_empty() -> None:
    """Uncovered path is a no-op when the config has zero uncovered tickers.

    WHI-794 still probes configured Hermes feed_ids for never-published rows.
    """
    from monitor.underlying.config import UnderlyingConfig

    cfg = load_underlying_config()
    # Zero uncovered: keep one covered ticker with a live feed shape.
    aapl = cfg.tickers["AAPL"]
    slim = UnderlyingConfig.model_validate(
        {
            **cfg.model_dump(mode="python"),
            "tickers": {
                "AAPL": aapl.model_dump(mode="python"),
            },
        }
    )
    assert slim.uncovered_tickers() == []

    class _Hermes:
        def search_price_feeds(self, query: str) -> list[object]:
            raise AssertionError("should not search hermes when uncovered empty")

        def fetch_latest(self, feed_ids: list[str], *, chunk_size: int = 20):  # noqa: ANN001
            # Healthy feed → no unpublished advisory.
            return {
                "parsed": [
                    {
                        "id": feed_ids[0],
                        "price": {
                            "price": "100",
                            "conf": "1",
                            "expo": 0,
                            "publish_time": 1_700_000_000,
                        },
                    }
                ]
            }

        def close(self) -> None:
            return None

    class _BoomYahoo:
        def fetch_chart(self, symbol: str) -> dict[str, object]:
            raise AssertionError("should not call yahoo when uncovered empty")

        def close(self) -> None:
            return None

    probe = UncoveredCoverageProbe(
        slim, hermes=_Hermes(), yahoo=_BoomYahoo()  # type: ignore[arg-type]
    )
    try:
        outcome = probe.probe_once()
        assert outcome.mismatches == []
        assert outcome.errors == []
        assert outcome.unpublished_feeds == []
    finally:
        probe.close()


def test_whi790_synthetic_bstock_underlyings_are_uncovered() -> None:
    """WHI-790: basket/unknown bStock labels stay uncovered (no single-name tape)."""
    cfg = load_underlying_config()
    # CBRS remains the only synthetic/unknown bStock label with no public tape.
    assert cfg.tickers["CBRS"].uncovered is True
    for t in ("DRAM", "INTW", "SNXX", "MVLL", "MUU", "KORU"):
        assert cfg.tickers[t].uncovered is False
        assert cfg.tickers[t].prefer_yahoo is True


def test_uncovered_probe_detects_yahoo_for_synthetic_uncovered(
    tmp_path: Path,
) -> None:
    """Live seam: uncovered config + Yahoo chart body → WARN-class mismatch."""
    from monitor.underlying.config import UnderlyingConfig
    from monitor.underlying.coverage_probe import UncoveredMismatch

    base = load_underlying_config()
    # Build a tiny config with one synthetic uncovered ticker.
    raw = base.model_dump()
    raw["tickers"] = {
        "SYNTH": {
            "currency": "USD",
            "pyth_symbol": None,
            "feed_id": None,
            "uncovered": True,
            "uncovered_reason": "test fixture",
        }
    }
    cfg = UnderlyingConfig.model_validate(raw)

    class FakeYahoo:
        def fetch_chart(self, symbol: str) -> dict[str, object]:
            assert symbol == "SYNTH"
            return {
                "chart": {
                    "result": [{"meta": {"regularMarketPrice": 42.0, "currency": "USD"}}]
                }
            }

        def close(self) -> None:
            return None

    class FakeHermes:
        def search_price_feeds(self, query: str) -> list[object]:
            return []

        def fetch_latest(self, feed_ids: list[str], *, chunk_size: int = 20):  # noqa: ANN001
            return {"parsed": []}

        def close(self) -> None:
            return None

    probe = UncoveredCoverageProbe(
        cfg, hermes=FakeHermes(), yahoo=FakeYahoo()  # type: ignore[arg-type]
    )
    try:
        outcome = probe.probe_once()
    finally:
        probe.close()
    assert len(outcome.mismatches) == 1
    assert isinstance(outcome.mismatches[0], UncoveredMismatch)
    assert outcome.mismatches[0].ticker == "SYNTH"
    assert outcome.mismatches[0].sources == ("yahoo",)
    assert outcome.errors == []
    assert outcome.unpublished_feeds == []


def test_uncovered_probe_records_errors_not_false_clear() -> None:
    """Both sources fail → errors populated, no mismatch (not 'all clear')."""
    from monitor.underlying.config import UnderlyingConfig

    base = load_underlying_config()
    raw = base.model_dump()
    raw["tickers"] = {
        "GHOST": {
            "currency": "USD",
            "uncovered": True,
            "uncovered_reason": "test",
        }
    }
    cfg = UnderlyingConfig.model_validate(raw)

    class FailYahoo:
        def fetch_chart(self, symbol: str) -> dict[str, object]:
            raise TimeoutError("yahoo down")

        def close(self) -> None:
            return None

    class FailHermes:
        def search_price_feeds(self, query: str) -> list[object]:
            raise TimeoutError("hermes down")

        def fetch_latest(self, feed_ids: list[str], *, chunk_size: int = 20):  # noqa: ANN001
            # No feed_ids in this synthetic-only config.
            return {"parsed": []}

        def close(self) -> None:
            return None

    probe = UncoveredCoverageProbe(
        cfg, hermes=FailHermes(), yahoo=FailYahoo()  # type: ignore[arg-type]
    )
    try:
        outcome = probe.probe_once()
    finally:
        probe.close()
    assert outcome.mismatches == []
    assert len(outcome.errors) == 2
    assert outcome.inconclusive is True
    sources = {e.source for e in outcome.errors}
    assert sources == {"yahoo", "pyth_hermes"}
    assert outcome.unpublished_feeds == []


def test_stamp_underlying_poll_meta_even_when_empty(tmp_path: Path) -> None:
    """WHI-788: empty poll still writes last_poll_ms / last_n (ops observability)."""
    from monitor.collector.config import load_collector_config
    from monitor.collector.daemon import (
        UNDERLYING_STATUS_RUNNING,
        CollectorDaemon,
    )
    from monitor.symbols import load_pairs_config

    store = SqliteStore(tmp_path / "meta.db")
    daemon = CollectorDaemon(
        load_pairs_config(),
        load_collector_config(market_id="bybit-fluxion"),
        store,
        market_id="bybit-fluxion",
    )
    daemon._stamp_underlying_status(
        UNDERLYING_STATUS_RUNNING, error="", tickers=["AAPL", "TSLA"]
    )
    daemon._stamp_underlying_poll(0, poll_ms=1_700_000_000_000)
    assert store.get_meta("underlying_status") == UNDERLYING_STATUS_RUNNING
    assert store.get_meta("underlying_tickers") == "AAPL,TSLA"
    assert store.get_meta("underlying_last_poll_ms") == "1700000000000"
    assert store.get_meta("underlying_last_n") == "0"
    assert store.get_meta("underlying_last_error") == ""

    daemon._stamp_underlying_poll(0, error="poll error: boom", poll_ms=1_700_000_000_100)
    assert store.get_meta("underlying_last_n") == "0"
    assert store.get_meta("underlying_last_error") == "poll error: boom"
    # Empty successful poll must NOT wipe the prior error trail.
    daemon._stamp_underlying_poll(0, poll_ms=1_700_000_000_150)
    assert store.get_meta("underlying_last_error") == "poll error: boom"

    daemon._stamp_underlying_poll(5, poll_ms=1_700_000_000_200)
    assert store.get_meta("underlying_last_n") == "5"
    assert store.get_meta("underlying_last_error") == ""
    store.close()


def test_stamp_underlying_status_early_exits(tmp_path: Path) -> None:
    """WHI-788: disabled / no_tickers / stopped leave meta for ops diagnosis."""
    from monitor.collector.config import load_collector_config
    from monitor.collector.daemon import (
        UNDERLYING_STATUS_CONFIG_ERROR,
        UNDERLYING_STATUS_DISABLED,
        UNDERLYING_STATUS_NO_TICKERS,
        UNDERLYING_STATUS_STOPPED,
        CollectorDaemon,
    )
    from monitor.symbols import load_pairs_config

    store = SqliteStore(tmp_path / "status.db")
    daemon = CollectorDaemon(
        load_pairs_config(),
        load_collector_config(market_id="bybit-fluxion"),
        store,
        market_id="bybit-fluxion",
    )
    daemon._stamp_underlying_status(UNDERLYING_STATUS_DISABLED)
    assert store.get_meta("underlying_status") == "disabled"
    daemon._stamp_underlying_status(UNDERLYING_STATUS_NO_TICKERS)
    assert store.get_meta("underlying_status") == "no_tickers"
    daemon._stamp_underlying_status(
        UNDERLYING_STATUS_CONFIG_ERROR, error="missing yaml"
    )
    assert store.get_meta("underlying_status") == "config_error"
    assert store.get_meta("underlying_last_error") == "missing yaml"
    # finally-path contract: loop exit must not leave status stuck at running.
    daemon._stamp_underlying_status(UNDERLYING_STATUS_STOPPED)
    assert store.get_meta("underlying_status") == "stopped"
    store.close()


def test_hermes_fetch_latest_chunks_and_ignore_invalid() -> None:
    """WHI-790: large feed lists are chunked; invalid ids must not fail the batch."""
    from monitor.underlying.pyth import HermesClient

    calls: list[list[str]] = []

    class _FakeResp:
        def __init__(self, body: dict) -> None:
            self._body = body

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._body

    class _FakeClient:
        def get(self, url: str, params=None):  # noqa: ANN001
            ids = [v for k, v in (params or []) if k == "ids[]"]
            calls.append(ids)
            # Assert ignore flag present
            flags = [v for k, v in (params or []) if k == "ignore_invalid_price_ids"]
            assert flags == ["true"]
            parsed = [
                {
                    "id": i,
                    "price": {
                        "price": "1",
                        "expo": 0,
                        "conf": "0",
                        "publish_time": 1,
                    },
                }
                for i in ids
            ]
            return _FakeResp({"parsed": parsed})

        def close(self) -> None:
            return None

    client = HermesClient(base_url="https://hermes.example", client=_FakeClient())  # type: ignore[arg-type]
    try:
        ids = [f"{i:064x}" for i in range(25)]
        body = client.fetch_latest(ids, chunk_size=10)
        assert len(calls) == 3  # 10+10+5
        assert len(body["parsed"]) == 25
    finally:
        client.close()


# --- WHI-794: Pyth unpublished feeds (price=0 / publish_time=0) ---


def test_parse_hermes_rejects_unpublished_zero_price() -> None:
    """Hermes registered-but-never-published: price=0, publish_time=0 → no tick.

    Acceptance (WHI-794): must not invent a 0.0000 / as_of_ms=0 journal row.
    """
    cfg = load_underlying_config()
    soxl_id = cfg.tickers["SOXL"].feed_id
    assert soxl_id is not None
    body = {
        "parsed": [
            {
                "id": soxl_id,
                "price": {
                    "price": "0",
                    "conf": "0",
                    "expo": -5,
                    "publish_time": 0,
                },
            }
        ]
    }
    now = _ms(2026, 8, 2, 12, 0)
    ticks, by_feed = parse_hermes_latest(
        body,
        cfg=cfg,
        tickers={"SOXL"},
        recv_ts_ms=now,
        now_ms_value=now,
    )
    assert ticks == []
    # Invalid quotes must not pollute the feed price map either.
    assert soxl_id.lower().removeprefix("0x") not in by_feed


def test_parse_hermes_rejects_nonpositive_price_even_with_publish_time() -> None:
    cfg = load_underlying_config()
    aapl_id = cfg.tickers["AAPL"].feed_id
    assert aapl_id is not None
    publish = int(_ms(2026, 7, 31, 16, 0) / 1000)
    body = {
        "parsed": [
            {
                "id": aapl_id,
                "price": {
                    "price": "0",
                    "conf": "1",
                    "expo": -5,
                    "publish_time": publish,
                },
            }
        ]
    }
    now = _ms(2026, 8, 2, 12, 0)
    ticks, by_feed = parse_hermes_latest(
        body, cfg=cfg, tickers={"AAPL"}, recv_ts_ms=now, now_ms_value=now
    )
    assert ticks == []
    assert aapl_id.lower().removeprefix("0x") not in by_feed


def test_poller_yahoo_fallback_when_hermes_unpublished() -> None:
    """Invalid Hermes row must not block Yahoo gap-fill (WHI-794)."""
    from monitor.underlying.config import UnderlyingConfig
    from monitor.underlying.poller import UnderlyingPoller

    base = load_underlying_config()
    soxl_id = base.tickers["SOXL"].feed_id
    assert soxl_id is not None
    # Tiny config: SOXL still has feed_id + yahoo_symbol for fallback.
    raw = base.model_dump()
    raw["tickers"] = {
        "SOXL": {
            "currency": "USD",
            "pyth_symbol": "Equity.US.SOXL/USD",
            "feed_id": soxl_id,
            "yahoo_symbol": "SOXL",
            "prefer_yahoo": False,
        }
    }
    cfg = UnderlyingConfig.model_validate(raw)

    class FakeHermes:
        def fetch_latest(self, feed_ids: list[str], *, chunk_size: int = 20):  # noqa: ANN001
            return {
                "parsed": [
                    {
                        "id": soxl_id,
                        "price": {
                            "price": "0",
                            "conf": "0",
                            "expo": -5,
                            "publish_time": 0,
                        },
                    }
                ]
            }

        def close(self) -> None:
            return None

    class FakeYahoo:
        def fetch_chart(self, symbol: str) -> dict[str, object]:
            assert symbol == "SOXL"
            as_of_s = int(_ms(2026, 7, 31, 16, 0) / 1000)
            return {
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "regularMarketPrice": 114.72,
                                "regularMarketTime": as_of_s,
                                "currency": "USD",
                                "marketState": "CLOSED",
                            }
                        }
                    ]
                }
            }

        def close(self) -> None:
            return None

    poller = UnderlyingPoller(
        cfg,
        tickers=["SOXL"],
        hermes=FakeHermes(),  # type: ignore[arg-type]
        yahoo=FakeYahoo(),  # type: ignore[arg-type]
    )
    try:
        now = _ms(2026, 8, 2, 12, 0)
        ticks = poller.poll_once(now_ms_value=now)
    finally:
        poller.close()
    assert len(ticks) == 1
    t = ticks[0]
    assert t.ticker == "SOXL"
    assert t.source == "yahoo"
    assert t.price == Decimal("114.72")
    assert t.as_of_ms > 0


def test_detect_unpublished_pyth_feeds() -> None:
    from monitor.underlying.coverage_probe import detect_unpublished_pyth_feeds

    cfg = load_underlying_config()
    soxl_id = cfg.tickers["SOXL"].feed_id
    aapl_id = cfg.tickers["AAPL"].feed_id
    assert soxl_id and aapl_id
    body = {
        "parsed": [
            {
                "id": soxl_id,
                "price": {"price": "0", "conf": "0", "expo": -5, "publish_time": 0},
            },
            {
                "id": aapl_id,
                "price": {
                    "price": "30985484",
                    "conf": "1",
                    "expo": -5,
                    "publish_time": 1_700_000_000,
                },
            },
        ]
    }
    found = detect_unpublished_pyth_feeds(body, cfg=cfg, tickers={"SOXL", "AAPL"})
    assert len(found) == 1
    assert found[0].ticker == "SOXL"
    assert found[0].publish_time == 0


def test_unpublished_tickers_have_yahoo_symbol() -> None:
    """Audit set (WHI-794): registered-but-never-published feeds must gap-fill."""
    cfg = load_underlying_config()
    # Live probe 2026-08-03: these six Hermes feeds return price=0/publish_time=0.
    for name in ("AAOI", "AXTI", "BE", "EWY", "NBIS", "SOXL"):
        t = cfg.tickers[name]
        assert t.uncovered is False, name
        assert t.yahoo_symbol, f"{name} needs yahoo_symbol for gap-fill"


def test_detect_unpublished_sets_gap_filled_when_yahoo_configured() -> None:
    from monitor.underlying.coverage_probe import detect_unpublished_pyth_feeds

    cfg = load_underlying_config()
    soxl_id = cfg.tickers["SOXL"].feed_id
    assert soxl_id is not None
    assert cfg.tickers["SOXL"].yahoo_symbol
    body = {
        "parsed": [
            {
                "id": soxl_id,
                "price": {"price": "0", "conf": "0", "expo": -5, "publish_time": 0},
            }
        ]
    }
    found = detect_unpublished_pyth_feeds(body, cfg=cfg, tickers={"SOXL"})
    assert len(found) == 1
    assert found[0].gap_filled is True
    assert "Yahoo gap-fill" in found[0].detail


def test_poller_skips_hermes_miss_gap_fill_on_transport_failure() -> None:
    """Hermes raise must not fan out Yahoo for every yahoo_symbol pin."""
    from monitor.underlying.config import UnderlyingConfig
    from monitor.underlying.poller import UnderlyingPoller

    base = load_underlying_config()
    soxl_id = base.tickers["SOXL"].feed_id
    assert soxl_id is not None
    raw = base.model_dump()
    raw["tickers"] = {
        "SOXL": {
            "currency": "USD",
            "feed_id": soxl_id,
            "yahoo_symbol": "SOXL",
            "prefer_yahoo": False,
        },
        "SPCX": {
            "currency": "USD",
            "feed_id": None,
            "yahoo_symbol": "SPCX",
            "prefer_yahoo": True,
        },
    }
    cfg = UnderlyingConfig.model_validate(raw)
    yahoo_calls: list[str] = []

    class FailHermes:
        def fetch_latest(self, feed_ids: list[str], *, chunk_size: int = 20):  # noqa: ANN001
            raise TimeoutError("hermes down")

        def close(self) -> None:
            return None

    class TrackYahoo:
        def fetch_chart(self, symbol: str) -> dict[str, object]:
            yahoo_calls.append(symbol)
            as_of_s = int(_ms(2026, 7, 31, 16, 0) / 1000)
            return {
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "regularMarketPrice": 100.0,
                                "regularMarketTime": as_of_s,
                                "currency": "USD",
                                "marketState": "CLOSED",
                            }
                        }
                    ]
                }
            }

        def close(self) -> None:
            return None

    poller = UnderlyingPoller(
        cfg,
        tickers=["SOXL", "SPCX"],
        hermes=FailHermes(),  # type: ignore[arg-type]
        yahoo=TrackYahoo(),  # type: ignore[arg-type]
    )
    try:
        ticks = poller.poll_once(now_ms_value=_ms(2026, 8, 2, 12, 0))
    finally:
        poller.close()
    # prefer_yahoo still runs; Hermes-miss gap-fill for SOXL does not.
    assert yahoo_calls == ["SPCX"]
    assert [t.ticker for t in ticks] == ["SPCX"]


def test_stamp_unpublished_meta_skips_on_hermes_latest_failure(tmp_path: Path) -> None:
    """WHI-794: Hermes transport error must not blank prior unpublished meta."""
    from monitor.collector.config import load_collector_config
    from monitor.collector.daemon import CollectorDaemon
    from monitor.symbols import load_pairs_config
    from monitor.underlying.coverage_probe import (
        META_UNPUBLISHED,
        ProbeError,
        ProbeOutcome,
        UnpublishedFeed,
        unpublished_from_meta_json,
        unpublished_to_meta_json,
    )

    store = SqliteStore(tmp_path / "stamp.db")
    prior = [
        UnpublishedFeed(
            ticker="SOXL",
            feed_id="abc",
            publish_time=0,
            price="0",
            detail="prior",
            gap_filled=True,
        )
    ]
    store.set_meta(META_UNPUBLISHED, unpublished_to_meta_json(prior))
    daemon = CollectorDaemon(
        load_pairs_config(),
        load_collector_config(market_id="bybit-fluxion"),
        store,
        market_id="bybit-fluxion",
    )
    daemon._stamp_uncovered_probe(
        ProbeOutcome(
            mismatches=[],
            errors=[ProbeError(ticker="*", source="pyth_hermes_latest", error="timeout")],
            unpublished_feeds=[],
            hermes_latest_ok=False,
        ),
        probe_ms=1_700_000_000_000,
    )
    kept = unpublished_from_meta_json(store.get_meta(META_UNPUBLISHED))
    assert len(kept) == 1
    assert kept[0]["ticker"] == "SOXL"
    store.close()


def test_probe_cli_exit_zero_when_unpublished_already_gap_filled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI exit 0 when unpublished pins already have yahoo_symbol (soft-ack)."""
    from monitor.underlying import __main__ as umain
    from monitor.underlying.coverage_probe import (
        ProbeOutcome,
        UnpublishedFeed,
    )

    class FakeProbe:
        def __init__(self, cfg) -> None:  # noqa: ANN001
            self.cfg = cfg

        def probe_once(self) -> ProbeOutcome:
            return ProbeOutcome(
                mismatches=[],
                errors=[],
                unpublished_feeds=[
                    UnpublishedFeed(
                        ticker="SOXL",
                        feed_id="5300",
                        publish_time=0,
                        price="0",
                        detail="gap fill ok",
                        gap_filled=True,
                    )
                ],
                hermes_latest_ok=True,
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(umain, "UncoveredCoverageProbe", FakeProbe)
    assert umain.main(["--probe-uncovered"]) == 0
