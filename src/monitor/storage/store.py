"""SQLite writer for collector ticks (M2 / WHI-731)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Sequence
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
from monitor.storage.schema import DDL, SCHEMA_VERSION


class SqliteStore:
    """Append-only store with explicit gap rows and per-row gap flags.

    One connection is shared across collector threads; all writes take ``_lock``.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.parent != Path(".") and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> SqliteStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _init_schema(self) -> None:
        with self._lock:
            with self._conn:
                for stmt in DDL:
                    self._conn.execute(stmt)
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("schema_version", str(SCHEMA_VERSION)),
                )

    def _insert_many(self, sql: str, rows: Sequence[tuple[object, ...]]) -> int:
        if not rows:
            return 0
        with self._lock:
            with self._conn:
                self._conn.executemany(sql, rows)
        return len(rows)

    def insert_bybit_book(self, ticks: Iterable[BybitBookTick]) -> int:
        rows = [
            (
                t.pair_id,
                t.symbol,
                t.exchange_ts_ms,
                t.recv_ts_ms,
                str(t.bid),
                str(t.ask),
                str(t.bid_de_multiplied),
                str(t.ask_de_multiplied),
                str(t.multiplier),
                1 if t.gap else 0,
            )
            for t in ticks
        ]
        return self._insert_many(
            """
            INSERT INTO bybit_book (
                pair_id, symbol, exchange_ts_ms, recv_ts_ms,
                bid, ask, bid_de_multiplied, ask_de_multiplied,
                multiplier, gap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def insert_bybit_trades(self, ticks: Iterable[BybitTradeTick]) -> int:
        rows = [
            (
                t.pair_id,
                t.symbol,
                t.exchange_ts_ms,
                t.recv_ts_ms,
                t.trade_id,
                str(t.price),
                str(t.price_de_multiplied),
                str(t.size),
                t.side,
                str(t.multiplier),
                1 if t.gap else 0,
            )
            for t in ticks
        ]
        return self._insert_many(
            """
            INSERT OR IGNORE INTO bybit_trades (
                pair_id, symbol, exchange_ts_ms, recv_ts_ms, trade_id,
                price, price_de_multiplied, size, side, multiplier, gap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def insert_pool_state(self, ticks: Iterable[FluxionPoolStateTick]) -> int:
        rows = [
            (
                t.pair_id,
                t.pool.lower(),
                t.block_number,
                t.block_ts,
                t.recv_ts_ms,
                str(t.sqrt_price_x96),
                t.tick,
                str(t.liquidity),
                t.token0.lower(),
                t.token1.lower(),
                str(t.mid_usdc_per_wrapper),
                str(t.mid_usdc_per_native),
                str(t.wrapper_assets_per_share),
                1 if t.gap else 0,
            )
            for t in ticks
        ]
        return self._insert_many(
            """
            INSERT OR IGNORE INTO fluxion_pool_state (
                pair_id, pool, block_number, block_ts, recv_ts_ms,
                sqrt_price_x96, tick, liquidity, token0, token1,
                mid_usdc_per_wrapper, mid_usdc_per_native,
                wrapper_assets_per_share, gap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def insert_swaps(self, ticks: Iterable[FluxionSwapTick]) -> int:
        rows = [
            (
                t.pair_id,
                t.pool.lower(),
                t.block_number,
                t.block_ts,
                t.recv_ts_ms,
                t.tx_hash.lower(),
                t.log_index,
                t.sender.lower(),
                t.recipient.lower(),
                str(t.amount0),
                str(t.amount1),
                str(t.sqrt_price_x96),
                str(t.liquidity),
                t.tick,
                str(t.amount_token0),
                str(t.amount_token1),
                t.direction,
                None if t.price_usdc_per_wrapper is None else str(t.price_usdc_per_wrapper),
                t.gas_used,
                t.effective_gas_price,
                1 if t.gap else 0,
            )
            for t in ticks
        ]
        return self._insert_many(
            """
            INSERT OR IGNORE INTO fluxion_swaps (
                pair_id, pool, block_number, block_ts, recv_ts_ms,
                tx_hash, log_index, sender, recipient,
                amount0, amount1, sqrt_price_x96, liquidity, tick,
                amount_token0, amount_token1, direction,
                price_usdc_per_wrapper, gas_used, effective_gas_price, gap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def insert_rfq_quotes(self, ticks: Iterable[FluxionRfqQuoteTick]) -> int:
        rows = [
            (
                t.pair_id,
                t.poll_ts_ms,
                t.recv_ts_ms,
                t.token_in.lower(),
                t.token_out.lower(),
                t.amount_in,
                t.amount_out,
                None if t.price is None else str(t.price),
                t.side,
                t.request_id,
                t.http_status,
                1 if t.available else 0,
                1 if t.gap else 0,
            )
            for t in ticks
        ]
        return self._insert_many(
            """
            INSERT INTO fluxion_rfq_quotes (
                pair_id, poll_ts_ms, recv_ts_ms, token_in, token_out,
                amount_in, amount_out, price, side, request_id,
                http_status, available, gap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def insert_rfq_fills(self, ticks: Iterable[FluxionRfqFillTick]) -> int:
        rows = [
            (
                t.block_number,
                t.block_ts,
                t.recv_ts_ms,
                t.tx_hash.lower(),
                t.log_index,
                t.order_hash.lower(),
                str(t.remaining_making_amount),
                1 if t.gap else 0,
            )
            for t in ticks
        ]
        return self._insert_many(
            """
            INSERT OR IGNORE INTO fluxion_rfq_fills (
                block_number, block_ts, recv_ts_ms, tx_hash, log_index,
                order_hash, remaining_making_amount, gap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def insert_gap(self, gap: CollectorGap) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO collector_gaps (source, gap_start_ms, gap_end_ms, detail)
                    VALUES (?, ?, ?, ?)
                    """,
                    (gap.source, gap.gap_start_ms, gap.gap_end_ms, gap.detail),
                )

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else str(row["value"])

    def count(self, table: str) -> int:
        if table not in {
            "bybit_book",
            "bybit_trades",
            "fluxion_pool_state",
            "fluxion_swaps",
            "fluxion_rfq_quotes",
            "fluxion_rfq_fills",
            "collector_gaps",
        }:
            raise ValueError(f"unknown table: {table}")
        with self._lock:
            row = self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])
