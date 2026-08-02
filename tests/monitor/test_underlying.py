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
from monitor.underlying.price_type import classify_price_type
from monitor.underlying.pyth import parse_hermes_latest, scale_pyth_price
from monitor.underlying.tickers import (
    pair_id_to_underlying_ticker,
    underlying_tickers_for_pairs,
)
from monitor.underlying.yahoo import market_state_hint, parse_yahoo_chart


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
    assert cfg.tickers["SPCX"].uncovered is True
    # WHI-785: SKHY is Nasdaq ADR (USD Yahoo), not KRX 000660.KS.
    skhy = cfg.tickers["SKHY"]
    assert skhy.prefer_yahoo is True
    assert skhy.yahoo_symbol == "SKHY"
    assert skhy.feed_id is None
    assert skhy.pyth_symbol is None
    assert cfg.fx_usd_krw_feed_id is None
    assert cfg.needs_fx({"SKHY"}) is False
    assert "AAPL" in cfg.covered_tickers()
    assert "SPCX" in cfg.uncovered_tickers()


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
    assert "SPCX" in tickers  # uncovered private; poller skips, no fake price
    cfg = load_underlying_config()
    covered = [t for t in tickers if t in cfg.tickers and not cfg.tickers[t].uncovered]
    assert covered, "no Hermes-covered tickers for bybit inventory"
    # SPCX stays uncovered (no fake price) — covered list already checked above.
    assert "SPCX" not in covered


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
