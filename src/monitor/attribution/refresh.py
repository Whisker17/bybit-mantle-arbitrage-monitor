"""Refresh address_labels + rebalance_events from the collector journal (WHI-768).

Usage::

    uv run python -m monitor.attribution.refresh
    uv run python -m monitor.attribution.refresh --sqlite data/monitor.db
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

from monitor.attribution.address_labels import (
    label_addresses_from_journal,
    persist_address_labels,
    persist_rebalance_events,
    rebalance_events_from_transfers,
)
from monitor.attribution.config import load_attribution_config
from monitor.collector.config import load_collector_config, load_dotenv
from monitor.quotes import Erc20TransferTick, FluxionRfqFillTick, FluxionSwapTick, now_ms
from monitor.storage import SqliteStore

logger = logging.getLogger(__name__)


def _dec(value: object | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def load_swaps(conn: sqlite3.Connection, *, limit: int = 50_000) -> list[FluxionSwapTick]:
    rows = conn.execute(
        """
        SELECT pair_id, pool, block_number, block_ts, recv_ts_ms, tx_hash, log_index,
               sender, recipient, amount0, amount1, sqrt_price_x96, liquidity, tick,
               amount_token0, amount_token1, direction, price_usdc_per_wrapper,
               gas_used, effective_gas_price, gap
        FROM fluxion_swaps
        ORDER BY block_number DESC, log_index DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out: list[FluxionSwapTick] = []
    for r in rows:
        out.append(
            FluxionSwapTick(
                pair_id=str(r["pair_id"]),
                pool=str(r["pool"]),
                block_number=int(r["block_number"]),
                block_ts=int(r["block_ts"]),
                recv_ts_ms=int(r["recv_ts_ms"]),
                tx_hash=str(r["tx_hash"]),
                log_index=int(r["log_index"]),
                sender=str(r["sender"]),
                recipient=str(r["recipient"]),
                amount0=int(str(r["amount0"])),
                amount1=int(str(r["amount1"])),
                sqrt_price_x96=int(str(r["sqrt_price_x96"])),
                liquidity=int(str(r["liquidity"])),
                tick=int(r["tick"]),
                amount_token0=Decimal(str(r["amount_token0"])),
                amount_token1=Decimal(str(r["amount_token1"])),
                direction=str(r["direction"]),  # type: ignore[arg-type]
                price_usdc_per_wrapper=_dec(r["price_usdc_per_wrapper"]),
                gas_used=None if r["gas_used"] is None else int(r["gas_used"]),
                effective_gas_price=(
                    None
                    if r["effective_gas_price"] is None
                    else int(r["effective_gas_price"])
                ),
                gap=bool(int(r["gap"] or 0)),
            )
        )
    return out


def load_rfq_fills(
    conn: sqlite3.Connection, *, limit: int = 50_000
) -> list[FluxionRfqFillTick]:
    rows = conn.execute(
        """
        SELECT block_number, block_ts, recv_ts_ms, tx_hash, log_index, order_hash,
               remaining_making_amount, pair_id, maker, taker, direction,
               making_token, taking_token, making_amount, taking_amount,
               usdc_amount, stock_amount, enriched, gap
        FROM fluxion_rfq_fills
        ORDER BY block_number DESC, log_index DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out: list[FluxionRfqFillTick] = []
    for r in rows:
        out.append(
            FluxionRfqFillTick(
                block_number=int(r["block_number"]),
                block_ts=int(r["block_ts"]),
                recv_ts_ms=int(r["recv_ts_ms"]),
                tx_hash=str(r["tx_hash"]),
                log_index=int(r["log_index"]),
                order_hash=str(r["order_hash"]),
                remaining_making_amount=int(str(r["remaining_making_amount"])),
                gap=bool(int(r["gap"] or 0)),
                pair_id=None if r["pair_id"] is None else str(r["pair_id"]),
                maker=None if r["maker"] is None else str(r["maker"]),
                taker=None if r["taker"] is None else str(r["taker"]),
                direction=None if r["direction"] is None else str(r["direction"]),
                making_token=(
                    None if r["making_token"] is None else str(r["making_token"])
                ),
                taking_token=(
                    None if r["taking_token"] is None else str(r["taking_token"])
                ),
                making_amount=(
                    None if r["making_amount"] is None else str(r["making_amount"])
                ),
                taking_amount=(
                    None if r["taking_amount"] is None else str(r["taking_amount"])
                ),
                usdc_amount=(
                    None if r["usdc_amount"] is None else str(r["usdc_amount"])
                ),
                stock_amount=(
                    None if r["stock_amount"] is None else str(r["stock_amount"])
                ),
                enriched=bool(int(r["enriched"] or 0)),
            )
        )
    return out


def load_transfers(
    conn: sqlite3.Connection, *, limit: int = 100_000
) -> list[Erc20TransferTick]:
    rows = conn.execute(
        """
        SELECT pair_id, token, block_number, block_ts, recv_ts_ms, tx_hash, log_index,
               frm, to_addr, amount, amount_raw, gap
        FROM erc20_transfers
        ORDER BY block_number DESC, log_index DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out: list[Erc20TransferTick] = []
    for r in rows:
        out.append(
            Erc20TransferTick(
                pair_id=str(r["pair_id"]),
                token=str(r["token"]),
                block_number=int(r["block_number"]),
                block_ts=int(r["block_ts"]),
                recv_ts_ms=int(r["recv_ts_ms"]),
                tx_hash=str(r["tx_hash"]),
                log_index=int(r["log_index"]),
                frm=str(r["frm"]),
                to_addr=str(r["to_addr"]),
                amount=Decimal(str(r["amount"])),
                amount_raw=int(str(r["amount_raw"])),
                gap=bool(int(r["gap"] or 0)),
            )
        )
    return out


def run_refresh(store: SqliteStore, *, updated_at_ms: int | None = None) -> dict[str, int]:
    """Load journal → label addresses → persist labels + rebalance events."""
    cfg = load_attribution_config()
    ts = updated_at_ms if updated_at_ms is not None else now_ms()
    with store._lock:
        swaps = load_swaps(store._conn)
        fills = load_rfq_fills(store._conn)
        transfers = load_transfers(store._conn)
    results = label_addresses_from_journal(
        swaps=swaps, rfq_fills=fills, transfers=transfers, config=cfg
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
