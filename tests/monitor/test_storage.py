"""Seams: SqliteStore insert/read, gap flags, schema bootstrap."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from monitor.quotes import (
    BybitBookTick,
    BybitTradeTick,
    CollectorGap,
    FluxionPoolStateTick,
    FluxionRfqFillTick,
    FluxionRfqQuoteTick,
    FluxionSwapTick,
)
from monitor.storage import SqliteStore
from monitor.storage.schema import SCHEMA_VERSION


def test_schema_bootstrap_and_meta(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    with SqliteStore(db) as store:
        assert store.get_meta("schema_version") == str(SCHEMA_VERSION)
        store.set_meta("last_block", "123")
        assert store.get_meta("last_block") == "123"


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
