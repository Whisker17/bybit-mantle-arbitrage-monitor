"""SQLite writer for collector ticks (M2 / WHI-731)."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from monitor.quotes import (
    BybitBookTick,
    BybitDepthTick,
    BybitTradeTick,
    CexVolumeTick,
    CollectorGap,
    Erc20TransferTick,
    FluxionPoolStateTick,
    FluxionRfqFillTick,
    FluxionRfqQuoteTick,
    FluxionSwapTick,
)
from monitor.storage.schema import DDL, RFQ_FILL_V4_COLUMNS, SCHEMA_VERSION

if TYPE_CHECKING:
    from monitor.collector.config import RetentionConfig
    from monitor.storage.retention import GrowthSnapshot, RetentionReport


class SqliteStore:
    """Append-only store with explicit gap rows and per-row gap flags.

    One connection is shared across collector threads; all writes take ``_lock``.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.parent != Path(".") and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # auto_vacuum=INCREMENTAL only takes effect on a brand-new file (before
        # the first CREATE TABLE). Existing DBs keep their mode; freelist reuse
        # still plateaus size, and `python -m monitor.retention --full-vacuum`
        # can shrink once (DESIGN §5.1).
        is_new = not self.path.exists() or self.path.stat().st_size == 0
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        if is_new:
            self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
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
                # Tables first, then column migrations, then indexes that may
                # reference migrated columns (pre-v4 fluxion_rfq_fills path).
                for stmt in DDL:
                    if _is_create_index(stmt):
                        continue
                    self._conn.execute(stmt)
                self._migrate_rfq_fill_columns()
                for stmt in DDL:
                    if _is_create_index(stmt):
                        self._conn.execute(stmt)
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("schema_version", str(SCHEMA_VERSION)),
                )

    def _migrate_rfq_fill_columns(self) -> None:
        """Add WHI-768 enrichment columns to pre-v4 fluxion_rfq_fills tables."""
        existing = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(fluxion_rfq_fills)")
        }
        if not existing:
            return
        for name, decl in RFQ_FILL_V4_COLUMNS:
            if name not in existing:
                self._conn.execute(
                    f"ALTER TABLE fluxion_rfq_fills ADD COLUMN {name} {decl}"
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

    def insert_bybit_depth(self, ticks: Iterable[BybitDepthTick]) -> int:
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
                t.depth_levels,
                json.dumps([str(q) for q in t.buckets_usd]),
                json.dumps([None if v is None else str(v) for v in t.bid_vwap_dm]),
                json.dumps([None if v is None else str(v) for v in t.ask_vwap_dm]),
                1 if t.gap else 0,
            )
            for t in ticks
        ]
        return self._insert_many(
            """
            INSERT INTO bybit_depth (
                pair_id, symbol, exchange_ts_ms, recv_ts_ms,
                bid, ask, bid_de_multiplied, ask_de_multiplied,
                multiplier, depth_levels, buckets_usd, bid_vwap_dm, ask_vwap_dm,
                gap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                None if t.pair_id is None else t.pair_id,
                None if t.maker is None else t.maker.lower(),
                None if t.taker is None else t.taker.lower(),
                t.direction,
                None if t.making_token is None else t.making_token.lower(),
                None if t.taking_token is None else t.taking_token.lower(),
                t.making_amount,
                t.taking_amount,
                t.usdc_amount,
                t.stock_amount,
                1 if t.enriched else 0,
                1 if t.gap else 0,
            )
            for t in ticks
        ]
        return self._insert_many(
            """
            INSERT OR IGNORE INTO fluxion_rfq_fills (
                block_number, block_ts, recv_ts_ms, tx_hash, log_index,
                order_hash, remaining_making_amount,
                pair_id, maker, taker, direction,
                making_token, taking_token, making_amount, taking_amount,
                usdc_amount, stock_amount, enriched, gap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def update_rfq_fill_enrichment(self, tick: FluxionRfqFillTick) -> bool:
        """Patch enrichment columns on an existing (tx_hash, log_index) row."""
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    UPDATE fluxion_rfq_fills SET
                        pair_id = ?,
                        maker = ?,
                        taker = ?,
                        direction = ?,
                        making_token = ?,
                        taking_token = ?,
                        making_amount = ?,
                        taking_amount = ?,
                        usdc_amount = ?,
                        stock_amount = ?,
                        enriched = ?
                    WHERE tx_hash = ? AND log_index = ?
                    """,
                    (
                        tick.pair_id,
                        None if tick.maker is None else tick.maker.lower(),
                        None if tick.taker is None else tick.taker.lower(),
                        tick.direction,
                        None
                        if tick.making_token is None
                        else tick.making_token.lower(),
                        None
                        if tick.taking_token is None
                        else tick.taking_token.lower(),
                        tick.making_amount,
                        tick.taking_amount,
                        tick.usdc_amount,
                        tick.stock_amount,
                        1 if tick.enriched else 0,
                        tick.tx_hash.lower(),
                        tick.log_index,
                    ),
                )
        return cur.rowcount > 0

    def unenriched_rfq_fills(self, *, limit: int = 500) -> list[dict[str, object]]:
        """Rows still missing receipt enrichment (for one-shot backfill)."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, block_number, block_ts, recv_ts_ms, tx_hash, log_index,
                       order_hash, remaining_making_amount, gap
                FROM fluxion_rfq_fills
                WHERE COALESCE(enriched, 0) = 0
                ORDER BY block_number ASC, log_index ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def insert_erc20_transfers(self, ticks: Iterable[Erc20TransferTick]) -> int:
        rows = [
            (
                t.pair_id,
                t.token.lower(),
                t.block_number,
                t.block_ts,
                t.recv_ts_ms,
                t.tx_hash.lower(),
                t.log_index,
                t.frm.lower(),
                t.to_addr.lower(),
                str(t.amount),
                str(t.amount_raw),
                1 if t.gap else 0,
            )
            for t in ticks
        ]
        return self._insert_many(
            """
            INSERT OR IGNORE INTO erc20_transfers (
                pair_id, token, block_number, block_ts, recv_ts_ms,
                tx_hash, log_index, frm, to_addr, amount, amount_raw, gap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def upsert_address_label(
        self,
        *,
        address: str,
        label: str,
        evidence_summary: str,
        first_seen_ms: int | None,
        last_seen_ms: int | None,
        source: str,
        is_rebalancer: bool = False,
        n_rfq_maker: int = 0,
        n_amm: int = 0,
        cex_touch_transfers: int = 0,
        updated_at_ms: int,
    ) -> None:
        """Insert or update one address label row (manual overrides win at write time)."""
        addr = address.lower()
        with self._lock:
            with self._conn:
                existing = self._conn.execute(
                    "SELECT source FROM address_labels WHERE address = ?",
                    (addr,),
                ).fetchone()
                if existing is not None and str(existing["source"]) == "manual":
                    # Config / operator overrides are sticky until deleted.
                    return
                self._conn.execute(
                    """
                    INSERT INTO address_labels (
                        address, label, evidence_summary, first_seen_ms, last_seen_ms,
                        source, is_rebalancer, n_rfq_maker, n_amm, cex_touch_transfers,
                        updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(address) DO UPDATE SET
                        label = excluded.label,
                        evidence_summary = excluded.evidence_summary,
                        first_seen_ms = COALESCE(
                            address_labels.first_seen_ms, excluded.first_seen_ms
                        ),
                        last_seen_ms = excluded.last_seen_ms,
                        source = excluded.source,
                        is_rebalancer = excluded.is_rebalancer,
                        n_rfq_maker = excluded.n_rfq_maker,
                        n_amm = excluded.n_amm,
                        cex_touch_transfers = excluded.cex_touch_transfers,
                        updated_at_ms = excluded.updated_at_ms
                    WHERE address_labels.source != 'manual'
                    """,
                    (
                        addr,
                        label,
                        evidence_summary,
                        first_seen_ms,
                        last_seen_ms,
                        source,
                        1 if is_rebalancer else 0,
                        n_rfq_maker,
                        n_amm,
                        cex_touch_transfers,
                        updated_at_ms,
                    ),
                )

    def upsert_manual_address_label(
        self,
        *,
        address: str,
        label: str,
        evidence_summary: str,
        updated_at_ms: int,
    ) -> None:
        """Force a manual override (wins over auto refresh)."""
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO address_labels (
                        address, label, evidence_summary, first_seen_ms, last_seen_ms,
                        source, is_rebalancer, n_rfq_maker, n_amm, cex_touch_transfers,
                        updated_at_ms
                    ) VALUES (?, ?, ?, NULL, NULL, 'manual', 0, 0, 0, 0, ?)
                    ON CONFLICT(address) DO UPDATE SET
                        label = excluded.label,
                        evidence_summary = excluded.evidence_summary,
                        source = 'manual',
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (address.lower(), label, evidence_summary, updated_at_ms),
                )

    def insert_rebalance_events(
        self,
        rows: Iterable[tuple[object, ...]],
    ) -> int:
        """Insert rebalance event tuples matching the rebalance_events DDL."""
        return self._insert_many(
            """
            INSERT OR IGNORE INTO rebalance_events (
                address, counterparty, pair_id, token, amount, direction,
                block_number, block_ts, recv_ts_ms, tx_hash, log_index
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            list(rows),
        )

    def insert_cex_volume(self, ticks: Iterable[CexVolumeTick]) -> int:
        """Append CEX REST 24h volume snapshots (WHI-777)."""
        rows = [
            (
                t.pair_id,
                t.symbol,
                t.poll_ts_ms,
                t.recv_ts_ms,
                str(t.volume_quote_24h),
                t.trade_count_24h,
                t.source,
                1 if t.gap else 0,
            )
            for t in ticks
        ]
        return self._insert_many(
            """
            INSERT INTO cex_volume_24h (
                pair_id, symbol, poll_ts_ms, recv_ts_ms,
                volume_quote_24h, trade_count_24h, source, gap
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
            "bybit_book_1m",
            "bybit_depth",
            "bybit_trades",
            "fluxion_pool_state",
            "fluxion_swaps",
            "fluxion_rfq_quotes",
            "fluxion_rfq_fills",
            "erc20_transfers",
            "address_labels",
            "rebalance_events",
            "collector_gaps",
            "cex_volume_24h",
        }:
            raise ValueError(f"unknown table: {table}")
        with self._lock:
            row = self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])

    def _write(self, fn: object) -> object:
        """Run ``fn(conn)`` under the store lock inside a short transaction.

        Each call commits before releasing the lock so long retention loops can
        interleave with collector inserts (WHI-751).
        """
        if not callable(fn):
            raise TypeError("write fn must be callable")
        cb = fn
        with self._lock:
            with self._conn:
                return cb(self._conn)

    def _reclaim(self, fn: object) -> object:
        """Run reclaim (checkpoint / vacuum) under the lock, no open transaction."""
        if not callable(fn):
            raise TypeError("reclaim fn must be callable")
        cb = fn
        with self._lock:
            self._conn.commit()
            return cb(self._conn)

    def run_retention(
        self,
        cfg: RetentionConfig,
        *,
        now_ms: int,
        free_bytes: int | None = None,
    ) -> RetentionReport:
        """Apply retention in short locked batches (safe vs live inserts)."""
        # Local import avoids a hard cycle: retention → collector.config, store → retention.
        from monitor.storage.retention import run_retention as _run

        return _run(
            self._conn,
            cfg,
            now_ms=now_ms,
            free_bytes=free_bytes,
            db_path=self.path,
            write=self._write,
            reclaim=self._reclaim,
        )

    def growth_snapshot(self) -> GrowthSnapshot:
        """Per-table growth report (read under the store lock)."""
        from monitor.storage.retention import growth_snapshot as _snap

        with self._lock:
            return _snap(self._conn, db_path=self.path)


def _is_create_index(stmt: str) -> bool:
    return stmt.lstrip().upper().startswith("CREATE INDEX")

