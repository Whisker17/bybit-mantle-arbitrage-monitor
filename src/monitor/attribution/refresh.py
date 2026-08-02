"""Refresh address_labels + rebalance_events from the collector journal (WHI-768).

Usage::

    uv run python -m monitor.attribution.refresh
    uv run python -m monitor.attribution.refresh --sqlite data/monitor.db
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from monitor.attribution.address_labels import (
    label_addresses_from_journal,
    persist_address_labels,
    persist_rebalance_events,
    rebalance_events_from_transfers,
)
from monitor.attribution.config import load_attribution_config
from monitor.collector.config import load_collector_config, load_dotenv
from monitor.quotes import now_ms
from monitor.storage import JournalReader, SqliteStore
from monitor.symbols import load_pairs_config
from monitor.symbols.token_map import quote_is_token0_by_pair

logger = logging.getLogger(__name__)


def run_refresh(
    store: SqliteStore,
    *,
    updated_at_ms: int | None = None,
    reader: JournalReader | None = None,
) -> dict[str, int]:
    """Load journal via JournalReader → label addresses → persist."""
    cfg = load_attribution_config()
    pairs = load_pairs_config()
    q0_map = quote_is_token0_by_pair(pairs)
    ts = updated_at_ms if updated_at_ms is not None else now_ms()
    jr = reader if reader is not None else JournalReader(store.path)
    close_reader = reader is None
    try:
        swaps = jr.recent_swaps()
        fills = jr.recent_rfq_fills()
        transfers = jr.recent_erc20_transfers()
    finally:
        if close_reader:
            jr.close()
    results = label_addresses_from_journal(
        swaps=swaps,
        rfq_fills=fills,
        transfers=transfers,
        config=cfg,
        quote_is_token0_by_pair=q0_map,
    )
    n_labels = persist_address_labels(store, results, updated_at_ms=ts)
    reb_events = rebalance_events_from_transfers(
        transfers,
        cfg.rebalancer.cex_wallets,
        min_amount=cfg.rebalancer.min_transfer_notional_native,
    )
    n_reb = persist_rebalance_events(store, reb_events)
    by_label: dict[str, int] = {}
    for r in results:
        by_label[r.label.value] = by_label.get(r.label.value, 0) + 1
    logger.info(
        "refresh labels=%s rebalance_events=%s distribution=%s",
        n_labels,
        n_reb,
        by_label,
    )
    return {
        "labels": n_labels,
        "rebalance_events": n_reb,
        "addresses": len(results),
        **{f"label_{k}": v for k, v in by_label.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh address_labels from collector journal (WHI-768)"
    )
    parser.add_argument("--sqlite", type=Path, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()
    collector = load_collector_config()
    db = args.sqlite or collector.resolved_sqlite_path()
    store = SqliteStore(db)
    try:
        stats = run_refresh(store)
        print(f"attribution refresh: {stats} db={db}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
