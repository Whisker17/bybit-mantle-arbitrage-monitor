"""SQLite journal retention: prune, downsample, reclaim, disk waterline (WHI-751).

Consumer windows that must remain intact after a prune (DESIGN §5.1):

- TUI 24h volume → ``bybit_trades`` + ``fluxion_swaps`` (swaps never pruned)
- TUI cold-start EdgeStats / sparklines → recent ``bybit_book`` + pool + RFQ
  (capped at ``edge_history_max_samples``; raw TTL ≥ a few hours is enough)
- M4 attribution → ``fluxion_swaps`` / ``fluxion_rfq_fills`` permanent

Before deleting raw ``bybit_book`` rows older than the raw TTL, the last L1 of
each minute is upserted into ``bybit_book_1m`` so a compact series survives
(forensics / future cold-start; live JournalReader still uses raw ticks).
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from monitor.collector.config import DiskGuardConfig, RetentionConfig

logger = logging.getLogger(__name__)

DiskLevel = Literal["ok", "warn", "critical"]
# Short-transaction runner: store lock + commit around each batch.
WriteFn = Callable[[Callable[[sqlite3.Connection], Any]], Any]

# One place to declare pruneable tables + their wall-clock column.
# ``ttl_attr`` names the RetentionConfig / EffectiveTtls field (None policy = never).
_TABLE_POLICIES: tuple[tuple[str, str, str], ...] = (
    # table, ts_column, ttl_attr
    ("bybit_book", "exchange_ts_ms", "bybit_book_raw_ms"),
    ("bybit_book_1m", "bucket_ts_ms", "bybit_book_1m_ms"),
    ("bybit_trades", "exchange_ts_ms", "bybit_trades_ms"),
    ("fluxion_pool_state", "recv_ts_ms", "fluxion_pool_state_ms"),
    ("fluxion_rfq_quotes", "poll_ts_ms", "fluxion_rfq_quotes_ms"),
    ("fluxion_swaps", "recv_ts_ms", "fluxion_swaps_ms"),
    ("fluxion_rfq_fills", "recv_ts_ms", "fluxion_rfq_fills_ms"),
    ("collector_gaps", "gap_start_ms", "collector_gaps_ms"),
)

_PRUNE_TABLES = frozenset(t for t, _, _ in _TABLE_POLICIES)
_TS_COLUMNS = frozenset(c for _, c, _ in _TABLE_POLICIES)
_GROWTH_TABLES: tuple[str, ...] = tuple(t for t, _, _ in _TABLE_POLICIES)


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

    def get(self, attr: str) -> int | None:
        return getattr(self, attr)  # type: ignore[no-any-return]


@dataclass(frozen=True, slots=True)
class RetentionReport:
    now_ms: int
    free_bytes: int | None
    disk_level: DiskLevel
    deleted: dict[str, int] = field(default_factory=dict)
    bars_upserted: int = 0
    vacuum_pages_requested: int = 0
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
    approx_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class GrowthSnapshot:
    """Per-table row counts + crude rows/day from each table's timestamp span."""

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


def _delete_batch(
    conn: sqlite3.Connection,
    *,
    table: str,
    ts_column: str,
    cutoff_ms: int,
    batch_size: int,
) -> int:
    """Delete up to ``batch_size`` oldest rows with ts < cutoff. Returns rows removed."""
    if table not in _PRUNE_TABLES:
        raise ValueError(f"refusing to prune unknown table: {table}")
    if ts_column not in _TS_COLUMNS:
        raise ValueError(f"refusing unknown ts column: {ts_column}")
    # rowid works for both INTEGER PK tables and composite-PK bybit_book_1m.
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
    return n


def _materialize_book_1m_range(
    conn: sqlite3.Connection,
    *,
    range_lo_ms: int,
    range_hi_ms: int,
) -> int:
    """Upsert last-in-minute L1 for raw books in ``[range_lo_ms, range_hi_ms)``.

    Only complete minute buckets with ``bucket_end <= range_hi_ms`` are written,
    so a minute that straddles the prune cutoff is left for a later pass (avoids
    undercounting ``n`` when the same minute is re-materialized).
    """
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
            WHERE exchange_ts_ms >= ?
              AND exchange_ts_ms < ?
              AND (exchange_ts_ms / 60000) * 60000 + 60000 <= ?
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
        (range_lo_ms, range_hi_ms, range_hi_ms),
    )
    n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    return n


def _auto_vacuum_mode(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA auto_vacuum").fetchone()
    return int(row[0]) if row is not None else 0


def _reclaim_space(
    conn: sqlite3.Connection,
    *,
    incremental_pages: int,
    full_vacuum: bool,
) -> tuple[int, bool]:
    """Return (incremental pages requested if applicable, whether full VACUUM ran)."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    pages_req = 0
    mode = _auto_vacuum_mode(conn)
    # 2 = INCREMENTAL. NONE/FULL make incremental_vacuum a no-op.
    if incremental_pages > 0 and mode == 2:
        try:
            conn.execute(f"PRAGMA incremental_vacuum({int(incremental_pages)})")
            pages_req = incremental_pages
        except sqlite3.Error as exc:
            logger.warning("incremental_vacuum failed: %s", exc)
    elif incremental_pages > 0 and mode != 2:
        logger.info(
            "skip incremental_vacuum: auto_vacuum mode=%s (need INCREMENTAL=2); "
            "freelist pages are still reused for new inserts; use full_vacuum to shrink",
            mode,
        )
    did_full = False
    if full_vacuum:
        try:
            conn.execute("VACUUM")
            did_full = True
        except sqlite3.Error as exc:
            logger.warning("VACUUM failed (writer busy?): %s", exc)
    return pages_req, did_full


def run_retention(
    conn: sqlite3.Connection,
    cfg: RetentionConfig,
    *,
    now_ms: int,
    free_bytes: int | None = None,
    db_path: Path | None = None,
    write: WriteFn | None = None,
    reclaim: WriteFn | None = None,
) -> RetentionReport:
    """Prune tables per policy.

    When ``write`` is provided (store lock + short transaction), each batch
    commits and releases so the collector can interleave inserts. Without
    ``write``, work runs directly on ``conn``.

    ``reclaim`` must run outside an open transaction (VACUUM requirement).
    When omitted, reclaim runs on ``conn`` after an explicit commit.
    """
    # Prefer explicit free_bytes (tests); else config probe path; else sqlite parent.
    if free_bytes is None:
        if cfg.disk.path:
            free_bytes = disk_free_bytes(Path(cfg.disk.path))
        elif db_path is not None:
            free_bytes = disk_free_bytes(db_path)

    level: DiskLevel = "ok"
    if free_bytes is not None:
        level = classify_disk(free_bytes, cfg.disk)

    ttls = effective_ttls(cfg, level)
    deleted: dict[str, int] = {}
    bars = 0

    def _run(fn: Callable[[sqlite3.Connection], Any]) -> Any:
        if write is not None:
            return write(fn)
        return fn(conn)

    # Materialize 1m bars in time chunks before deleting raw books (lock-friendly).
    raw_ttl = ttls.bybit_book_raw_ms
    if raw_ttl is not None:
        cutoff = now_ms - raw_ttl
        # Chunk size: 6h of raw tape per write() — bounds lock hold vs round-trips.
        chunk_ms = 6 * 3_600_000

        def _min_ts(c: sqlite3.Connection) -> int | None:
            row = c.execute(
                "SELECT MIN(exchange_ts_ms) FROM bybit_book WHERE exchange_ts_ms < ?",
                (cutoff,),
            ).fetchone()
            if row is None or row[0] is None:
                return None
            return int(row[0])

        min_ts = _run(_min_ts)
        if min_ts is not None:
            lo = int(min_ts)
            while lo < cutoff:

                def _mat(
                    c: sqlite3.Connection,
                    *,
                    _lo: int = lo,
                    _hi: int = min(lo + chunk_ms, cutoff),
                ) -> int:
                    return _materialize_book_1m_range(
                        c, range_lo_ms=_lo, range_hi_ms=_hi
                    )

                bars += int(_run(_mat))
                lo += chunk_ms

    for table, ts_col, ttl_attr in _TABLE_POLICIES:
        ttl = ttls.get(ttl_attr)
        if ttl is None:
            continue
        cutoff = now_ms - ttl
        total = 0
        while True:

            def _batch(
                c: sqlite3.Connection,
                *,
                _table: str = table,
                _col: str = ts_col,
                _cut: int = cutoff,
            ) -> int:
                return _delete_batch(
                    c,
                    table=_table,
                    ts_column=_col,
                    cutoff_ms=_cut,
                    batch_size=cfg.delete_batch_size,
                )

            n = int(_run(_batch))
            total += n
            if n < cfg.delete_batch_size:
                break
        deleted[table] = total

    def _meta(c: sqlite3.Connection) -> None:
        c.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("retention_last_run_ms", str(now_ms)),
        )
        c.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("retention_last_disk_level", level),
        )

    _run(_meta)

    force_full = cfg.full_vacuum or level == "critical"

    def _vac(c: sqlite3.Connection) -> tuple[int, bool]:
        return _reclaim_space(
            c,
            incremental_pages=cfg.incremental_vacuum_pages,
            full_vacuum=force_full,
        )

    # VACUUM cannot run inside an explicit transaction.
    if reclaim is not None:
        vac_result = reclaim(_vac)
        assert isinstance(vac_result, tuple) and len(vac_result) == 2
        vacuum_pages = int(vac_result[0])
        did_full = bool(vac_result[1])
    else:
        conn.commit()
        vacuum_pages, did_full = _vac(conn)

    pause = level == "critical" and cfg.disk.pause_book_writes_on_critical

    report = RetentionReport(
        now_ms=now_ms,
        free_bytes=free_bytes,
        disk_level=level,
        deleted=deleted,
        bars_upserted=bars,
        vacuum_pages_requested=vacuum_pages,
        full_vacuum=did_full,
        book_writes_paused=pause,
    )
    logger.info(
        "retention done level=%s free=%s deleted=%s bars=%s pause_book=%s "
        "vacuum_pages_req=%s full_vacuum=%s",
        report.disk_level,
        report.free_bytes,
        report.deleted,
        report.bars_upserted,
        report.book_writes_paused,
        report.vacuum_pages_requested,
        report.full_vacuum,
    )
    return report


def _ts_column_for(table: str) -> str | None:
    for t, col, _ in _TABLE_POLICIES:
        if t == table:
            return col
    return None


def growth_snapshot(conn: sqlite3.Connection, *, db_path: Path | None = None) -> GrowthSnapshot:
    """Quantify rows, optional dbstat bytes, and crude rows/day per table."""
    # dbstat is available when SQLite is compiled with SQLITE_ENABLE_DBSTAT_VTAB.
    has_dbstat = False
    try:
        conn.execute("SELECT 1 FROM dbstat LIMIT 1")
        has_dbstat = True
    except sqlite3.Error:
        has_dbstat = False

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
        approx: int | None = None
        if col is not None and n > 0:
            bounds = conn.execute(
                f"SELECT MIN({col}) AS lo, MAX({col}) AS hi FROM {table}"
            ).fetchone()
            min_ts = int(bounds[0]) if bounds[0] is not None else None
            max_ts = int(bounds[1]) if bounds[1] is not None else None
            if min_ts is not None and max_ts is not None and max_ts > min_ts:
                span = max_ts - min_ts
                rate = n / (span / 86_400_000.0)
        if has_dbstat and n > 0:
            try:
                b = conn.execute(
                    "SELECT SUM(pgsize) FROM dbstat WHERE name = ?", (table,)
                ).fetchone()
                if b is not None and b[0] is not None:
                    approx = int(b[0])
            except sqlite3.Error:
                approx = None
        tables.append(
            TableGrowth(
                table=table,
                rows=n,
                min_ts_ms=min_ts,
                max_ts_ms=max_ts,
                span_ms=span,
                rows_per_day=rate,
                approx_bytes=approx,
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


def format_growth_report(
    snap: GrowthSnapshot,
    *,
    free_bytes: int | None = None,
) -> str:
    lines = [
        "table | rows | approx_bytes | span_h | rows/day",
        "--- | ---: | ---: | ---: | ---:",
    ]
    for t in snap.tables:
        span_h = "" if t.span_ms is None else f"{t.span_ms / 3_600_000:.2f}"
        rate = "" if t.rows_per_day is None else f"{t.rows_per_day:.0f}"
        abytes = "" if t.approx_bytes is None else str(t.approx_bytes)
        lines.append(f"{t.table} | {t.rows} | {abytes} | {span_h} | {rate}")
    if snap.db_bytes is not None:
        lines.append(f"\ndb_bytes: {snap.db_bytes} ({snap.db_bytes / (1024**2):.1f} MiB)")
    if free_bytes is not None:
        lines.append(
            f"disk_free: {free_bytes} ({free_bytes / (1024**2):.1f} MiB)"
        )
    book = next((t for t in snap.tables if t.table == "bybit_book"), None)
    if book is not None and book.rows_per_day and book.rows > 0 and snap.db_bytes:
        total_rows = sum(t.rows for t in snap.tables) or 1
        book_share = book.rows / total_rows
        if book.approx_bytes is not None:
            bytes_per_book_row = book.approx_bytes / max(book.rows, 1)
        else:
            bytes_per_book_row = (snap.db_bytes * book_share) / max(book.rows, 1)
        mb_per_day = book.rows_per_day * bytes_per_book_row / (1024**2)
        lines.append(
            f"approx bybit_book growth: {mb_per_day:.1f} MiB/day "
            f"(~{bytes_per_book_row:.0f} B/row × {book.rows_per_day:.0f} rows/day)"
        )
        if mb_per_day > 0 and free_bytes is not None and free_bytes > 0:
            free_mib = free_bytes / (1024**2)
            days = free_mib / mb_per_day
            lines.append(
                f"ungoverned: ~{days:.0f} days to fill current free disk "
                f"({free_mib:.0f} MiB) at this book rate "
                "(order-of-magnitude; open hours are faster — see DESIGN §5.1)"
            )
    return "\n".join(lines)
