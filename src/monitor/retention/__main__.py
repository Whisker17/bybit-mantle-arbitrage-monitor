"""CLI: run retention once and/or print growth stats.

Examples::

    python -m monitor.retention
    python -m monitor.retention --growth-only
    python -m monitor.retention --sqlite /path/to/monitor.db
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from monitor.collector.config import load_collector_config, load_dotenv
from monitor.quotes import now_ms
from monitor.storage import SqliteStore, format_growth_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prune collector SQLite journal (WHI-751 retention policy)"
    )
    parser.add_argument(
        "--collector-config",
        type=Path,
        default=None,
        help="Path to collector.yaml (default: config/collector.yaml)",
    )
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=None,
        help="Override SQLite path (default: collector.yaml sqlite_path)",
    )
    parser.add_argument(
        "--growth-only",
        action="store_true",
        help="Print per-table growth snapshot and exit without pruning",
    )
    parser.add_argument(
        "--now-ms",
        type=int,
        default=None,
        help="Override wall clock (ms) for deterministic runs/tests",
    )
    parser.add_argument(
        "--full-vacuum",
        action="store_true",
        help="Force VACUUM this run (blocks writers briefly)",
    )
    args = parser.parse_args(argv)

    load_dotenv()
    collector = load_collector_config(args.collector_config)
    db_path = args.sqlite or collector.resolved_sqlite_path()
    logging.basicConfig(
        level=getattr(logging, collector.logging.level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if not db_path.is_file():
        print(f"journal not found: {db_path}", file=sys.stderr)
        return 2

    store = SqliteStore(db_path)
    try:
        print(format_growth_report(store.growth_snapshot()))
        if args.growth_only:
            return 0

        cfg = collector.retention
        if args.full_vacuum:
            cfg = cfg.model_copy(update={"full_vacuum": True})
        if not cfg.enabled and not args.full_vacuum:
            print("retention.enabled=false; use --full-vacuum or enable in config")
            return 0

        wall_ms = args.now_ms if args.now_ms is not None else now_ms()
        report = store.run_retention(cfg, now_ms=wall_ms)
        print(
            f"retention: level={report.disk_level} free={report.free_bytes} "
            f"deleted={report.deleted} bars={report.bars_upserted} "
            f"pause_book={report.book_writes_paused}"
        )
        print("--- after ---")
        print(format_growth_report(store.growth_snapshot()))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
