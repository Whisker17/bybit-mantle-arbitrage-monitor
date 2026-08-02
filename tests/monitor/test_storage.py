"""Seams: SqliteStore insert/read, gap flags, schema bootstrap."""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from monitor.quotes import (
    BybitBookTick,
    BybitDepthTick,
    BybitTradeTick,
    CexVolumeTick,
    CollectorGap,
    DexPoolTvlTick,
    FluxionPoolStateTick,
    FluxionRfqFillTick,
    FluxionRfqQuoteTick,
    FluxionSwapTick,
    UnderlyingPriceTick,
)
from monitor.storage import JournalReader, SqliteStore
from monitor.storage.schema import SCHEMA_VERSION

# Optional tables guarded by JournalReader._table_names() (schema v5–v7).
_OPTIONAL_TABLES = ("dex_pool_tvl", "cex_volume_24h", "underlying_prices")


def test_schema_bootstrap_and_meta(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    with SqliteStore(db) as store:
        assert store.get_meta("schema_version") == str(SCHEMA_VERSION)
        store.set_meta("last_block", "123")
        assert store.get_meta("last_block") == "123"


def test_optional_table_accessors_refresh_after_late_create(tmp_path: Path) -> None:
    """WHI-789: same JournalReader must see tables created after first probe.

    API keeps one reader for the process life. If optional tables (dex_pool_tvl,
    cex_volume_24h, underlying_prices) appear only after the collector migrates
    a journal that was empty at API start, accessors must not stay stuck on the
    first negative sqlite_master snapshot.
    """
    db = tmp_path / "late_tables.db"
    with SqliteStore(db) as store:
        assert store.get_meta("schema_version") == str(SCHEMA_VERSION)

    # Simulate "table absent" at API start (the production failure mode). We
    # DROP while leaving schema_version at current SCHEMA_VERSION — this is not
    # a version-gated pre-v7 migration, but SqliteStore re-runs unconditional
    # CREATE IF NOT EXISTS, so late recreate still matches the real collector path.
    conn = sqlite3.connect(db)
    try:
        for name in _OPTIONAL_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {name}")
        conn.commit()
    finally:
        conn.close()

    with JournalReader(db) as reader:
        # First probes: tables absent → empty / None (and used to poison cache).
        assert reader.latest_pool_tvl("TSLAx") is None
        assert reader.pool_tvl_series("TSLAx") == []
        assert reader.latest_cex_volume("TSLAx") is None
        assert reader.latest_underlying_price("TSLA") is None
        assert reader.latest_underlying_prices(["TSLA"]) == {}
        assert reader.underlying_prices("TSLA") == []

        # Collector restarts / migrates: recreate schema + insert samples.
        with SqliteStore(db) as store:
            tvl = DexPoolTvlTick(
                pair_id="TSLAx",
                pool="0x" + "ab" * 20,
                block_number=42,
                block_ts=1_700_000_000,
                recv_ts_ms=1_700_000_000_500,
                base_bal=Decimal("10"),
                quote_bal=Decimal("1000"),
                base_price=Decimal("250"),
                tvl_usd=Decimal("3500"),
                gap=False,
            )
            assert store.insert_pool_tvl([tvl]) == 1
            cex = CexVolumeTick(
                pair_id="TSLAx",
                symbol="TSLAXUSDT",
                poll_ts_ms=200,
                recv_ts_ms=201,
                volume_quote_24h=Decimal("99"),
                trade_count_24h=3,
                source="bybit",
            )
            assert store.insert_cex_volume([cex]) == 1
            und = UnderlyingPriceTick(
                ticker="TSLA",
                price=Decimal("255"),
                currency="USD",
                price_type="live",
                as_of_ms=2_000,
                recv_ts_ms=2_010,
                source="pyth_hermes",
            )
            assert store.insert_underlying_prices([und]) == 1

        # Same reader instance must now surface the late-created tables.
        got_tvl = reader.latest_pool_tvl("TSLAx")
        assert got_tvl is not None
        assert got_tvl.tvl_usd == Decimal("3500")
        series = reader.pool_tvl_series("TSLAx")
        assert len(series) == 1
        assert series[0].tvl_usd == Decimal("3500")

        got_cex = reader.latest_cex_volume("TSLAx")
        assert got_cex is not None
        assert got_cex.volume_quote_24h == Decimal("99")

        got_und = reader.latest_underlying_price("TSLA")
        assert got_und is not None
        assert got_und.price == Decimal("255")
        batch = reader.latest_underlying_prices(["TSLA", "AAPL"])
        assert "TSLA" in batch
        assert "AAPL" not in batch
        hist = reader.underlying_prices("TSLA", limit=10)
        assert len(hist) == 1
        assert hist[0].price == Decimal("255")


def test_insert_pool_tvl_and_reader(tmp_path: Path) -> None:
    db = tmp_path / "tvl.db"
    store = SqliteStore(db)
    tick = DexPoolTvlTick(
        pair_id="TSLAx",
        pool="0x" + "ab" * 20,
        block_number=100,
        block_ts=1_700_000_000,
        recv_ts_ms=1_700_000_000_500,
        base_bal=Decimal("10"),
        quote_bal=Decimal("1000"),
        base_price=Decimal("250"),
        tvl_usd=Decimal("3500"),
        gap=False,
    )
    assert store.insert_pool_tvl([tick]) == 1
    assert store.count("dex_pool_tvl") == 1
    # duplicate (pool, block) ignored
    assert store.insert_pool_tvl([tick]) == 1
    assert store.count("dex_pool_tvl") == 1
    store.close()

    with JournalReader(db) as reader:
        got = reader.latest_pool_tvl("TSLAx")
        assert got is not None
        assert got.tvl_usd == Decimal("3500")
        assert got.base_bal == Decimal("10")
        series = reader.pool_tvl_series("TSLAx")
        assert len(series) == 1
        assert series[0].tvl_usd == Decimal("3500")


def test_insert_bybit_depth_and_reader_vwap(tmp_path: Path) -> None:
    db = tmp_path / "depth.db"
    store = SqliteStore(db)
    depth = BybitDepthTick(
        pair_id="TSLAx",
        symbol="TSLAXUSDT",
        exchange_ts_ms=1_700_000_000_000,
        recv_ts_ms=1_700_000_000_050,
        bid=Decimal("100"),
        ask=Decimal("101"),
        bid_de_multiplied=Decimal("100"),
        ask_de_multiplied=Decimal("101"),
        multiplier=Decimal("1"),
        depth_levels=3,
        buckets_usd=(Decimal("10"), Decimal("50"), Decimal("100")),
        bid_vwap_dm=(Decimal("100"), Decimal("99.5"), None),
        ask_vwap_dm=(Decimal("101"), Decimal("101.2"), Decimal("102")),
        gap=False,
    )
    assert store.insert_bybit_depth([depth]) == 1
    assert store.count("bybit_depth") == 1
    store.close()

    with JournalReader(db) as reader:
        got = reader.latest_bybit_depth("TSLAx")
        assert got is not None
        assert got.bid_vwap_dm[0] == Decimal("100")
        assert got.bid_vwap_dm[2] is None
        assert reader.bybit_depth_vwap("TSLAx", size_usd=Decimal("50"), side="bid") == (
            Decimal("99.5")
        )
        assert reader.bybit_depth_vwap("TSLAx", size_usd=Decimal("100"), side="bid") is None
        assert reader.bybit_depth_vwap("TSLAx", size_usd=Decimal("999"), side="ask") is None


def test_insert_bybit_book_and_trades_with_gap(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "t.db")
    book = BybitBookTick(
        pair_id="TSLAx",
        symbol="TSLAXUSDT",
        exchange_ts_ms=1_700_000_000_000,
        recv_ts_ms=1_700_000_000_050,
        bid=Decimal("250.5"),
        ask=Decimal("250.7"),
        bid_de_multiplied=Decimal("250.5"),
        ask_de_multiplied=Decimal("250.7"),
        multiplier=Decimal("1"),
        gap=True,
    )
    trade = BybitTradeTick(
        pair_id="TSLAx",
        symbol="TSLAXUSDT",
        exchange_ts_ms=1_700_000_000_100,
        recv_ts_ms=1_700_000_000_120,
        trade_id="abc",
        price=Decimal("250.6"),
        price_de_multiplied=Decimal("250.6"),
        size=Decimal("1.5"),
        side="Buy",
        multiplier=Decimal("1"),
        gap=True,
    )
    assert store.insert_bybit_book([book]) == 1
    assert store.insert_bybit_trades([trade]) == 1
    # duplicate trade ignored
    assert store.insert_bybit_trades([trade]) == 1
    assert store.count("bybit_book") == 1
    assert store.count("bybit_trades") == 1
    row = store._conn.execute("SELECT gap FROM bybit_book").fetchone()
    assert row["gap"] == 1
    store.close()


def test_insert_pool_state_swaps_rfq_and_gap(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "t.db")
    state = FluxionPoolStateTick(
        pair_id="TSLAx",
        pool="0x" + "11" * 20,
        block_number=100,
        block_ts=1_700_000_000,
        recv_ts_ms=1_700_000_000_500,
        sqrt_price_x96=2**96,
        tick=0,
        liquidity=10**18,
        token0="0x" + "22" * 20,
        token1="0x" + "33" * 20,
        mid_usdc_per_wrapper=Decimal("250"),
        mid_usdc_per_native=Decimal("250"),
        wrapper_assets_per_share=Decimal("1"),
        gap=False,
    )
    swap = FluxionSwapTick(
        pair_id="TSLAx",
        pool=state.pool,
        block_number=100,
        block_ts=1_700_000_000,
        recv_ts_ms=1_700_000_000_600,
        tx_hash="0x" + "ab" * 32,
        log_index=0,
        sender="0x" + "aa" * 20,
        recipient="0x" + "bb" * 20,
        amount0=10**6,
        amount1=-(10**18),
        sqrt_price_x96=2**96,
        liquidity=10**18,
        tick=0,
        amount_token0=Decimal("1"),
        amount_token1=Decimal("-1"),
        direction="buy_native",
        price_usdc_per_wrapper=Decimal("250"),
        gas_used=150_000,
        effective_gas_price=75_000_000_000,
        gap=False,
    )
    quote = FluxionRfqQuoteTick(
        pair_id="TSLAx",
        poll_ts_ms=1,
        recv_ts_ms=2,
        token_in="0x" + "09" * 20,
        token_out="0x" + "08" * 20,
        amount_in="100000000",
        amount_out="400000000000000000",
        price=Decimal("250"),
        side="buy",
        request_id="req-1",
        http_status=200,
        available=True,
    )
    fill = FluxionRfqFillTick(
        block_number=100,
        block_ts=1_700_000_000,
        recv_ts_ms=1_700_000_000_700,
        tx_hash="0x" + "cd" * 32,
        log_index=1,
        order_hash="0x" + "ee" * 32,
        remaining_making_amount=0,
    )
    assert store.insert_pool_state([state]) == 1
    assert store.insert_swaps([swap]) == 1
    assert store.insert_rfq_quotes([quote]) == 1
    assert store.insert_rfq_fills([fill]) == 1
    store.insert_gap(
        CollectorGap(
            source="bybit_ws",
            gap_start_ms=1,
            gap_end_ms=2,
            detail="test",
        )
    )
    assert store.count("fluxion_pool_state") == 1
    assert store.count("fluxion_swaps") == 1
    assert store.count("fluxion_rfq_quotes") == 1
    assert store.count("fluxion_rfq_fills") == 1
    assert store.count("collector_gaps") == 1
    store.close()
