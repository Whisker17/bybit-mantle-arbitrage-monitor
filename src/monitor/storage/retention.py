"""SQLite journal retention: prune, downsample, reclaim, disk waterline (WHI-751).

Consumer windows that must remain intact after a prune (DESIGN §5.1):

- TUI 24h volume → ``bybit_trades`` + ``fluxion_swaps`` (swaps never pruned)
- TUI cold-start EdgeStats / sparklines → recent ``bybit_book`` + pool + RFQ
  (capped at ``edge_history_max_samples``; raw TTL ≥ a few hours is enough)
- M4 attribution → ``fluxion_swaps`` / ``fluxion_rfq_fills`` permanent

Before deleting raw ``bybit_book`` rows older than the raw TTL, the last L1 of
each minute is upserted into ``bybit_book_1m`` so a compact series survives.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from monitor.collector.config import DiskGuardConfig, RetentionConfig

logger = logging.getLogger(__name__)

DiskLevel = Literal["ok", "warn", "critical"]

# Tables retention may touch (whitelist for dynamic SQL).
_PRUNE_TABLES = frozenset(
    {
        "bybit_book",
        "bybit_book_1m",
        "bybit_trades",
        "fluxion_pool_state",
        "fluxion_rfq_quotes",
        "fluxion_swaps",
        "fluxion_rfq_fills",
        "collector_gaps",
    }
)

_GROWTH_TABLES: tuple[str, ...] = (
    "bybit_book",
    "bybit_book_1m",
    "bybit_trades",
    "fluxion_pool_state",
    "fluxion_swaps",
    "fluxion_rfq_quotes",
    "fluxion_rfq_fills",
    "collector_gaps",
)


@dataclass(frozen=True, slots=True)
class EffectiveTtls:
    """Resolved TTLs after disk-level factoring (None = never prune)."""

    bybit_book_raw_ms: int | None
    bybit_book_1m_ms: int | None
    bybit_trades_ms: int | None
    fluxion_pool_state_ms: int | None
    fluxion_rfq_quotes_ms: int | None
    fluxion_swaps_ms: int | None
    fluxion_rfq_fills_ms: int | None
    collector_gaps_ms: int | None


@dataclass(frozen=True, slots=True)
class RetentionReport:
    now_ms: int
    free_bytes: int | None
    disk_level: DiskLevel
    deleted: dict[str, int] = field(default_factory=dict)
    bars_upserted: int = 0
    vacuum_pages: int = 0
    full_vacuum: bool = False
    book_writes_paused: bool = False


@dataclass(frozen=True, slots=True)
class TableGrowth:
    table: str
    rows: int
    min_ts_ms: int | None
    max_ts_ms: int | None
    span_ms: int | None
    rows_per_day: float | None


@dataclass(frozen=True, slots=True)
class GrowthSnapshot:
    """Per-table row counts + crude rate from min/max timestamp span."""

    tables: tuple[TableGrowth, ...]
    db_bytes: int | None


def disk_free_bytes(path: Path) -> int:
    """Free bytes on the filesystem that holds ``path`` (file or directory)."""
    target = path if path.is_dir() else path.parent
    if not target.exists():
        target = path.parent if path.parent.exists() else Path.cwd()
    return int(shutil.disk_usage(target).free)


def classify_disk(free_bytes: int, disk: DiskGuardConfig) -> DiskLevel:
    if free_bytes < disk.critical_free_bytes:
        return "critical"
    if free_bytes < disk.warn_free_bytes:
        return "warn"
    return "ok"


def _scale_ttl(ttl_ms: int | None, factor: float) -> int | None:
    if ttl_ms is None:
        return None
    # Floor at 60s so emergency prune never claims "keep zero".
    return max(60_000, int(ttl_ms * factor))


def effective_ttls(cfg: RetentionConfig, level: DiskLevel) -> EffectiveTtls:
    if level == "ok":
        factor = 1.0
    elif level == "warn":
        factor = cfg.disk.warn_ttl_factor
    else:
        factor = cfg.disk.critical_ttl_factor

    def s(v: int | None) -> int | None:
        return v if factor == 1.0 else _scale_ttl(v, factor)

    return EffectiveTtls(
        bybit_book_raw_ms=s(cfg.bybit_book_raw_ms),
        bybit_book_1m_ms=s(cfg.bybit_book_1m_ms),
        bybit_trades_ms=s(cfg.bybit_trades_ms),
        fluxion_pool_state_ms=s(cfg.fluxion_pool_state_ms),
        fluxion_rfq_quotes_ms=s(cfg.fluxion_rfq_quotes_ms),
        # Attribution feedstock: never accelerated — only an explicit non-null TTL.
        fluxion_swaps_ms=cfg.fluxion_swaps_ms,
        fluxion_rfq_fills_ms=cfg.fluxion_rfq_fills_ms,
        collector_gaps_ms=s(cfg.collector_gaps_ms),
    )


def _delete_older_than(
    conn: sqlite3.Connection,
    *,
    table: str,
    ts_column: str,
    cutoff_ms: int,
    batch_size: int,
) -> int:
    if table not in _PRUNE_TABLES:
        raise ValueError(f"refusing to prune unknown table: {table}")
    if ts_column not in {
        "exchange_ts_ms",
        "recv_ts_ms",
        "poll_ts_ms",
        "gap_start_ms",
        "bucket_ts_ms",
    }:
        raise ValueError(f"refusing unknown ts column: {ts_column}")

    total = 0
    # bybit_book_1m uses a composite PK (no surrogate id).
    has_id = table != "bybit_book_1m"
    while True:
        if has_id:
            cur = conn.execute(
                f"""
                DELETE FROM {table}
                WHERE id IN (
                    SELECT id FROM {table}
                    WHERE {ts_column} < ?
                    ORDER BY {ts_column} ASC
                    LIMIT ?
                )
                """,
                (cutoff_ms, batch_size),
            )
        else:
            cur = conn.execute(
                f"""
                DELETE FROM {table}
                WHERE rowid IN (
                    SELECT rowid FROM {table}
                    WHERE {ts_column} < ?
                    ORDER BY {ts_column} ASC
                    LIMIT ?
                )
                """,
                (cutoff_ms, batch_size),
            )
        n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        total += n
        if n < batch_size:
            break
    return total


def _materialize_book_1m(
    conn: sqlite3.Connection,
    *,
    cutoff_raw_ms: int,
) -> int:
    """Upsert last-in-minute L1 for every raw book row older than the raw cutoff."""
    # One row per (pair, minute): pick MAX(id) as the last tick in that bucket.
    cur = conn.execute(
        """
        INSERT INTO bybit_book_1m (
            pair_id, bucket_ts_ms, symbol, bid, ask,
            bid_de_multiplied, ask_de_multiplied, multiplier, n
        )
        SELECT
            b.pair_id,
            (b.exchange_ts_ms / 60000) * 60000 AS bucket_ts_ms,
            b.symbol,
            b.bid,
            b.ask,
            b.bid_de_multiplied,
            b.ask_de_multiplied,
            b.multiplier,
            c.n
        FROM (
            SELECT
                pair_id,
                (exchange_ts_ms / 60000) * 60000 AS bucket_ts_ms,
                MAX(id) AS max_id,
                COUNT(*) AS n
            FROM bybit_book
            WHERE exchange_ts_ms < ?
            GROUP BY pair_id, (exchange_ts_ms / 60000) * 60000
        ) AS c
        JOIN bybit_book AS b ON b.id = c.max_id
        ON CONFLICT(pair_id, bucket_ts_ms) DO UPDATE SET
            symbol = excluded.symbol,
            bid = excluded.bid,
            ask = excluded.ask,
            bid_de_multiplied = excluded.bid_de_multiplied,
            ask_de_multiplied = excluded.ask_de_multiplied,
            multiplier = excluded.multiplier,
            n = excluded.n
        """,
        (cutoff_raw_ms,),
    )
    n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    return n


def _reclaim_space(
    conn: sqlite3.Connection,
    *,
    incremental_pages: int,
    full_vacuum: bool,
) -> tuple[int, bool]:
    """Return (incremental pages attempted, whether full VACUUM ran)."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    pages = 0
    if incremental_pages > 0:
        # No-op when auto_vacuum is not INCREMENTAL; still safe.
        try:
            conn.execute(f"PRAGMA incremental_vacuum({int(incremental_pages)})")
            pages = incremental_pages
        except sqlite3.Error as exc:
            logger.warning("incremental_vacuum failed: %s", exc)
    did_full = False
    if full_vacuum:
        try:
            conn.execute("VACUUM")
            did_full = True
        except sqlite3.Error as exc:
            logger.warning("VACUUM failed (writer busy?): %s", exc)
    return pages, did_full


def run_retention(
    conn: sqlite3.Connection,
    cfg: RetentionConfig,
    *,
    now_ms: int,
    free_bytes: int | None = None,
    db_path: Path | None = None,
) -> RetentionReport:
    """Prune tables per policy. Caller holds the write lock if sharing a store."""
    if free_bytes is None and db_path is not None:
        free_bytes = disk_free_bytes(db_path)
    elif free_bytes is None and cfg.disk.path:
        free_bytes = disk_free_bytes(Path(cfg.disk.path))

    level: DiskLevel = "ok"
    if free_bytes is not None:
        level = classify_disk(free_bytes, cfg.disk)

    ttls = effective_ttls(cfg, level)
    deleted: dict[str, int] = {}
    bars = 0

    with conn:  # transaction for materialize + deletes
        if ttls.bybit_book_raw_ms is not None:
            cutoff = now_ms - ttls.bybit_book_raw_ms
            # Materialize before delete so the compact series survives.
            bars = _materialize_book_1m(conn, cutoff_raw_ms=cutoff)
            deleted["bybit_book"] = _delete_older_than(
                conn,
                table="bybit_book",
                ts_column="exchange_ts_ms",
                cutoff_ms=cutoff,
                batch_size=cfg.delete_batch_size,
            )

        if ttls.bybit_book_1m_ms is not None:
            deleted["bybit_book_1m"] = _delete_older_than(
                conn,
                table="bybit_book_1m",
                ts_column="bucket_ts_ms",
                cutoff_ms=now_ms - ttls.bybit_book_1m_ms,
                batch_size=cfg.delete_batch_size,
            )

        table_specs: list[tuple[str, str, int | None]] = [
            ("bybit_trades", "exchange_ts_ms", ttls.bybit_trades_ms),
            ("fluxion_pool_state", "recv_ts_ms", ttls.fluxion_pool_state_ms),
            ("fluxion_rfq_quotes", "poll_ts_ms", ttls.fluxion_rfq_quotes_ms),
            ("fluxion_swaps", "recv_ts_ms", ttls.fluxion_swaps_ms),
            ("fluxion_rfq_fills", "recv_ts_ms", ttls.fluxion_rfq_fills_ms),
            ("collector_gaps", "gap_start_ms", ttls.collector_gaps_ms),
        ]
        for table, col, ttl in table_specs:
            if ttl is None:
                continue
            deleted[table] = _delete_older_than(
                conn,
                table=table,
                ts_column=col,
                cutoff_ms=now_ms - ttl,
                batch_size=cfg.delete_batch_size,
            )

        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("retention_last_run_ms", str(now_ms)),
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("retention_last_disk_level", level),
        )

    vacuum_pages, did_full = _reclaim_space(
        conn,
        incremental_pages=cfg.incremental_vacuum_pages,
        full_vacuum=cfg.full_vacuum or level == "critical",
    )

    pause = (
        level == "critical"
        and cfg.disk.pause_book_writes_on_critical
        and (free_bytes is None or free_bytes < cfg.disk.critical_free_bytes)
    )

    report = RetentionReport(
        now_ms=now_ms,
        free_bytes=free_bytes,
        disk_level=level,
        deleted=deleted,
        bars_upserted=bars,
        vacuum_pages=vacuum_pages,
        full_vacuum=did_full,
        book_writes_paused=pause,
    )
    logger.info(
        "retention done level=%s free=%s deleted=%s bars=%s pause_book=%s",
        report.disk_level,
        report.free_bytes,
        report.deleted,
        report.bars_upserted,
        report.book_writes_paused,
    )
    return report


def _ts_column_for(table: str) -> str | None:
    return {
        "bybit_book": "exchange_ts_ms",
        "bybit_book_1m": "bucket_ts_ms",
        "bybit_trades": "exchange_ts_ms",
        "fluxion_pool_state": "recv_ts_ms",
        "fluxion_swaps": "recv_ts_ms",
        "fluxion_rfq_quotes": "poll_ts_ms",
        "fluxion_rfq_fills": "recv_ts_ms",
        "collector_gaps": "gap_start_ms",
    }.get(table)


def growth_snapshot(conn: sqlite3.Connection, *, db_path: Path | None = None) -> GrowthSnapshot:
    """Quantify rows and crude rows/day from each table's timestamp span."""
    tables: list[TableGrowth] = []
    for table in _GROWTH_TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if exists is None:
            tables.append(
                TableGrowth(
                    table=table,
                    rows=0,
                    min_ts_ms=None,
                    max_ts_ms=None,
                    span_ms=None,
                    rows_per_day=None,
                )
            )
            continue
        col = _ts_column_for(table)
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        n = int(row[0])
        min_ts: int | None = None
        max_ts: int | None = None
        span: int | None = None
        rate: float | None = None
        if col is not None and n > 0:
            bounds = conn.execute(
                f"SELECT MIN({col}) AS lo, MAX({col}) AS hi FROM {table}"
            ).fetchone()
            min_ts = int(bounds[0]) if bounds[0] is not None else None
            max_ts = int(bounds[1]) if bounds[1] is not None else None
            if min_ts is not None and max_ts is not None and max_ts > min_ts:
                span = max_ts - min_ts
                rate = n / (span / 86_400_000.0)
        tables.append(
            TableGrowth(
                table=table,
                rows=n,
                min_ts_ms=min_ts,
                max_ts_ms=max_ts,
                span_ms=span,
                rows_per_day=rate,
            )
        )
    db_bytes: int | None = None
    if db_path is not None and db_path.is_file():
        db_bytes = db_path.stat().st_size
        for suffix in ("-wal", "-shm"):
            side = Path(str(db_path) + suffix)
            if side.is_file():
                db_bytes += side.stat().st_size
    return GrowthSnapshot(tables=tuple(tables), db_bytes=db_bytes)


def format_growth_report(snap: GrowthSnapshot) -> str:
    lines = ["table | rows | span_h | rows/day", "--- | ---: | ---: | ---:"]
    for t in snap.tables:
        span_h = "" if t.span_ms is None else f"{t.span_ms / 3_600_000:.2f}"
        rate = "" if t.rows_per_day is None else f"{t.rows_per_day:.0f}"
        lines.append(f"{t.table} | {t.rows} | {span_h} | {rate}")
    if snap.db_bytes is not None:
        lines.append(f"\ndb_bytes: {snap.db_bytes} ({snap.db_bytes / (1024**2):.1f} MiB)")
    # Ungoverned exhaustion sketch using bybit_book rate if present.
    book = next((t for t in snap.tables if t.table == "bybit_book"), None)
    if book is not None and book.rows_per_day and book.rows > 0 and snap.db_bytes:
        # Attribute all current size to book proportionally (conservative upper bound).
        total_rows = sum(t.rows for t in snap.tables) or 1
        book_share = book.rows / total_rows
        bytes_per_book_row = (snap.db_bytes * book_share) / max(book.rows, 1)
        mb_per_day = book.rows_per_day * bytes_per_book_row / (1024**2)
        lines.append(
            f"approx bybit_book growth: {mb_per_day:.1f} MiB/day "
            f"(~{bytes_per_book_row:.0f} B/row × {book.rows_per_day:.0f} rows/day)"
        )
    return "\n".join(lines)
