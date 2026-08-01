"""Seam: JournalReader reconstructs quote ticks from collector SQLite."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from monitor.quotes import (
    BybitBookTick,
    FluxionPoolStateTick,
    FluxionRfqQuoteTick,
    FluxionSwapTick,
)
from monitor.storage import JournalReader, SqliteStore


def _rfq(side: str | None, *, price: str, ts: int) -> FluxionRfqQuoteTick:
    return FluxionRfqQuoteTick(
        pair_id="TSLAx",
        poll_ts_ms=ts,
        recv_ts_ms=ts + 1,
        token_in="0xusdc",
        token_out="0xtsla",
        amount_in="100000000",
        amount_out="1",
        price=Decimal(price),
        side=side,
        request_id="r1",
        http_status=200,
        available=True,
    )


def _seed(db: Path) -> None:
    store = SqliteStore(db)
    book = BybitBookTick(
        pair_id="TSLAx",
        symbol="TSLAXUSDT",
        exchange_ts_ms=1_700_000_000_000,
        recv_ts_ms=1_700_000_000_010,
        bid=Decimal("250"),
        ask=Decimal("250.2"),
        bid_de_multiplied=Decimal("250"),
        ask_de_multiplied=Decimal("250.2"),
        multiplier=Decimal(1),
    )
    pool = FluxionPoolStateTick(
        pair_id="TSLAx",
        pool="0x" + "11" * 20,
        block_number=10,
        block_ts=1_700_000_000,
        recv_ts_ms=1_700_000_000_020,
        sqrt_price_x96=2**96,
        tick=0,
        liquidity=10**18,
        token0="0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9",
        token1="0x" + "22" * 20,
        mid_usdc_per_wrapper=Decimal("249.5"),
        mid_usdc_per_native=Decimal("249.5"),
        wrapper_assets_per_share=Decimal(1),
    )
    swap = FluxionSwapTick(
        pair_id="TSLAx",
        pool=pool.pool,
        block_number=10,
        block_ts=1_700_000_000,
        recv_ts_ms=1_700_000_000_040,
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
        price_usdc_per_wrapper=Decimal("249.5"),
    )
    store.insert_bybit_book([book])
    store.insert_pool_state([pool])
    store.insert_rfq_quotes([_rfq("buy_native", price="249.8", ts=1_700_000_000_030)])
    store.insert_swaps([swap])
    store.close()


def test_reader_latest_ticks(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    _seed(db)
    with JournalReader(db) as r:
        book = r.latest_bybit_book("TSLAx")
        assert book is not None
        assert book.bid_de_multiplied == Decimal("250")
        pool = r.latest_pool_state("TSLAx")
        assert pool is not None
        assert pool.mid_usdc_per_native == Decimal("249.5")
        buy, sell = r.latest_rfq_sides("TSLAx")
        assert buy is not None and buy.price == Decimal("249.8")
        assert sell is None
        swaps = r.swaps("TSLAx", limit=10)
        assert len(swaps) == 1
        assert swaps[0].direction == "buy_native"
        vol = r.volume_stats("TSLAx", since_ms=0)
        assert vol.fluxion_swap_count == 1
        assert vol.bybit_trade_count == 0


def test_latest_rfq_leg_accepts_both_spellings(tmp_path: Path) -> None:
    """``buy``/``sell`` and ``buy_native``/``sell_native`` map to the same leg."""
    db = tmp_path / "m.db"
    _seed(db)
    store = SqliteStore(db)
    store.insert_rfq_quotes(
        [
            _rfq("sell", price="249.1", ts=1_700_000_000_050),
            _rfq("BUY_NATIVE", price="250.9", ts=1_700_000_000_060),
        ]
    )
    store.close()
    with JournalReader(db) as r:
        buy, sell = r.latest_rfq_sides("TSLAx")
        assert sell is not None and sell.price == Decimal("249.1")
        # Case-insensitive match, and the newer buy row wins.
        assert buy is not None and buy.price == Decimal("250.9")


def test_reader_since_ms_filters_history(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    _seed(db)
    with JournalReader(db) as r:
        assert r.bybit_books("TSLAx", since_ms=0)
        assert r.bybit_books("TSLAx", since_ms=1_700_000_000_001) == []
        assert r.swaps("TSLAx", since_ms=1_800_000_000_000) == []
        assert r.pool_states("TSLAx", since_ms=1_800_000_000_000) == []
        assert r.rfq_quotes("TSLAx", since_ms=1_800_000_000_000) == []


def test_reader_missing_db(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        JournalReader(tmp_path / "nope.db")
