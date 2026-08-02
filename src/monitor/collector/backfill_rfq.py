"""One-shot RFQ fill enrichment backfill (WHI-768).

Re-fetches receipts for unenriched ``fluxion_rfq_fills`` rows within the RPC
provider's log/receipt window (typically ≤30d) and patches maker/taker/pair.

Usage::

    uv run python -m monitor.collector.backfill_rfq
    uv run python -m monitor.collector.backfill_rfq --limit 200 --sqlite data/monitor.db
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from monitor.collector.config import (
    load_collector_config,
    load_dotenv,
    resolve_mantle_rpc_url,
)
from monitor.fluxion.rfq_decode import (
    apply_decoded_rfq_enrichment,
    decode_rfq_fill_from_receipt,
)
from monitor.fluxion.rpc import Rpc
from monitor.quotes import FluxionRfqFillTick
from monitor.storage import SqliteStore
from monitor.symbols import load_pairs_config
from monitor.symbols.token_map import inventory_token_to_pair

logger = logging.getLogger(__name__)


def enrich_fill_from_receipt(
    *,
    row: dict[str, object],
    logs: list[dict[str, object]],
    usdc: str,
    lop: str,
    token_to_pair: dict[str, str],
) -> FluxionRfqFillTick:
    """Build an enriched tick from a stored row + receipt logs."""
    base = FluxionRfqFillTick(
        block_number=int(row["block_number"]),
        block_ts=int(row["block_ts"]),
        recv_ts_ms=int(row["recv_ts_ms"]),
        tx_hash=str(row["tx_hash"]),
        log_index=int(row["log_index"]),
        order_hash=str(row["order_hash"]),
        remaining_making_amount=int(str(row["remaining_making_amount"])),
        gap=bool(int(row.get("gap") or 0)),
    )
    decoded = decode_rfq_fill_from_receipt(
        tx_hash=base.tx_hash,
        logs=logs,
        usdc=usdc,
        lop=lop,
        settlement_router=None,
        token_to_pair=token_to_pair,
    )
    return apply_decoded_rfq_enrichment(base, decoded, usdc=usdc)


def run_backfill(
    store: SqliteStore,
    rpc: Rpc,
    *,
    usdc: str,
    lop: str,
    token_to_pair: dict[str, str],
    limit: int = 500,
) -> tuple[int, int]:
    """Enrich up to ``limit`` unenriched rows. Returns (attempted, updated)."""
    rows = store.unenriched_rfq_fills(limit=limit)
    updated = 0
    for row in rows:
        txh = str(row["tx_hash"])
        try:
            rcpt = rpc.get_transaction_receipt(txh)
        except Exception as exc:  # noqa: BLE001
            logger.warning("receipt %s failed: %s", txh, exc)
            continue
        if not rcpt:
            logger.warning("receipt %s missing", txh)
            continue
        logs = rcpt.get("logs") or []
        if not isinstance(logs, list):
            continue
        tick = enrich_fill_from_receipt(
            row=row,
            logs=logs,  # type: ignore[arg-type]
            usdc=usdc,
            lop=lop,
            token_to_pair=token_to_pair,
        )
        if store.update_rfq_fill_enrichment(tick):
            updated += 1
            logger.info(
                "enriched fill %s maker=%s pair=%s",
                txh[:18],
                tick.maker,
                tick.pair_id,
            )
    return len(rows), updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill RFQ fill enrichment (WHI-768)"
    )
    parser.add_argument("--sqlite", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()
    collector = load_collector_config()
    pairs = load_pairs_config()
    db = args.sqlite or collector.resolved_sqlite_path()
    rpc_url = resolve_mantle_rpc_url(collector)
    store = SqliteStore(db)
    rpc = Rpc(
        rpc_url,
        multicall3=collector.mantle.multicall3,
        min_interval=collector.mantle.rpc_min_interval_s,
        timeout=collector.mantle.rpc_timeout_s,
        retries=collector.mantle.rpc_retries,
    )
    try:
        attempted, updated = run_backfill(
            store,
            rpc,
            usdc=pairs.contracts.usdc,
            lop=pairs.contracts.limit_order_protocol,
            token_to_pair=inventory_token_to_pair(pairs),
            limit=args.limit,
        )
        print(f"backfill_rfq: attempted={attempted} updated={updated} db={db}")
        return 0
    finally:
        rpc.close()
        store.close()


if __name__ == "__main__":
    sys.exit(main())
