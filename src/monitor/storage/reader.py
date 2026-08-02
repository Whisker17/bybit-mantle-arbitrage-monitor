"""Read-only view of the collector SQLite journal (M2 / WHI-731 schema).

Schema knowledge stays in ``monitor.storage`` (DESIGN §4.2): ``SqliteStore``
writes the journal, ``JournalReader`` reads it back as ``monitor.quotes`` shapes.
Consumers (M5 TUI) therefore never spell table or column names.

Opens its own read-only connection so the collector process keeps writing under
WAL.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from monitor.quotes import (
    RFQ_BUY_SIDES,
    RFQ_SELL_SIDES,
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
    RfqSideLeg,
)


@dataclass(frozen=True, slots=True)
class VolumeStats:
    """Rolling window volume / print counts for one pair."""

    bybit_trade_count: int
    bybit_notional: Decimal  # sum(price_de_multiplied * size) in quote units
    fluxion_swap_count: int


@dataclass(frozen=True, slots=True)
class AddressLabelRow:
    """One row from address_labels (WHI-768 productized labels)."""

    address: str
    label: str
    evidence_summary: str
    first_seen_ms: int | None
    last_seen_ms: int | None
    source: str
    is_rebalancer: bool
    n_rfq_maker: int
    n_amm: int
    cex_touch_transfers: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class RebalanceEventRow:
    """One CEX-touch rebalance event (WHI-768)."""

    address: str
    counterparty: str
    pair_id: str
    token: str
    amount: Decimal
    direction: str
    block_number: int
    block_ts: int
    recv_ts_ms: int
    tx_hash: str
    log_index: int


def _sides_for(leg: RfqSideLeg) -> frozenset[str]:
    return RFQ_BUY_SIDES if leg == "buy" else RFQ_SELL_SIDES


class JournalReader:
    """Read-only view of ``data/monitor.db`` (or any collector SQLite path)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"collector journal not found: {self.path}")
        # URI read-only keeps the writer free and fails cleanly if missing.
        uri = self.path.resolve().as_uri() + "?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Cached for optional tables (schema v5+); pre-v5 journals degrade cleanly.
        self._tables: frozenset[str] | None = None

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> JournalReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # --- latest snapshots -------------------------------------------------

    def latest_bybit_book(self, pair_id: str) -> BybitBookTick | None:
        row = self._conn.execute(
            """
            SELECT * FROM bybit_book
            WHERE pair_id = ?
            ORDER BY exchange_ts_ms DESC, id DESC
            LIMIT 1
            """,
            (pair_id,),
        ).fetchone()
        return None if row is None else _row_to_bybit_book(row)

    def latest_bybit_depth(self, pair_id: str) -> BybitDepthTick | None:
        """Latest precomputed multi-level VWAP curve for a pair (WHI-755)."""
        row = self._conn.execute(
            """
            SELECT * FROM bybit_depth
            WHERE pair_id = ?
            ORDER BY exchange_ts_ms DESC, id DESC
            LIMIT 1
            """,
            (pair_id,),
        ).fetchone()
        return None if row is None else _row_to_bybit_depth(row)

    def bybit_depth_vwap(
        self,
        pair_id: str,
        *,
        size_usd: Decimal,
        side: str,
    ) -> Decimal | None:
        """Look up precomputed VWAP for ``size_usd`` on ``side`` (bid|ask).

        Exact bucket match only — does not interpolate between ladder rungs.
        Returns None if no depth row, side invalid, unfillable, or size not in
        the stored bucket list.
        """
        if side not in ("bid", "ask"):
            raise ValueError(f"side must be bid|ask, got {side!r}")
        tick = self.latest_bybit_depth(pair_id)
        if tick is None:
            return None
        target = Decimal(str(size_usd))
        for i, q in enumerate(tick.buckets_usd):
            if q == target:
                curve = tick.bid_vwap_dm if side == "bid" else tick.ask_vwap_dm
                return curve[i]
        return None

    def latest_pool_state(self, pair_id: str) -> FluxionPoolStateTick | None:
        row = self._conn.execute(
            """
            SELECT * FROM fluxion_pool_state
            WHERE pair_id = ?
            ORDER BY block_number DESC, id DESC
            LIMIT 1
            """,
            (pair_id,),
        ).fetchone()
        return None if row is None else _row_to_pool_state(row)

    def latest_rfq_quote(
        self, pair_id: str, *, leg: RfqSideLeg
    ) -> FluxionRfqQuoteTick | None:
        """Latest polled quote for one Fluxion leg (either stored spelling)."""
        sides = sorted(_sides_for(leg))
        # Only the placeholder count is interpolated; every value stays bound.
        placeholders = ", ".join("?" for _ in sides)
        row = self._conn.execute(
            f"""
            SELECT * FROM fluxion_rfq_quotes
            WHERE pair_id = ? AND LOWER(side) IN ({placeholders})
            ORDER BY poll_ts_ms DESC, id DESC
            LIMIT 1
            """,
            (pair_id, *sides),
        ).fetchone()
        return None if row is None else _row_to_rfq_quote(row)

    def latest_rfq_sides(
        self, pair_id: str
    ) -> tuple[FluxionRfqQuoteTick | None, FluxionRfqQuoteTick | None]:
        """Return (buy leg, sell leg) latest available quotes."""
        return (
            self.latest_rfq_quote(pair_id, leg="buy"),
            self.latest_rfq_quote(pair_id, leg="sell"),
        )

    # --- history for sparklines / stats -----------------------------------

    def bybit_books(
        self,
        pair_id: str,
        *,
        since_ms: int = 0,
        limit: int = 500,
    ) -> list[BybitBookTick]:
        rows = self._conn.execute(
            """
            SELECT * FROM bybit_book
            WHERE pair_id = ? AND exchange_ts_ms >= ?
            ORDER BY exchange_ts_ms DESC, id DESC
            LIMIT ?
            """,
            (pair_id, since_ms, limit),
        ).fetchall()
        ticks = [_row_to_bybit_book(r) for r in rows]
        ticks.reverse()
        return ticks

    def pool_states(
        self,
        pair_id: str,
        *,
        since_ms: int = 0,
        limit: int = 500,
    ) -> list[FluxionPoolStateTick]:
        # block_ts is seconds; the window filters on recv_ts_ms wall clock.
        rows = self._conn.execute(
            """
            SELECT * FROM fluxion_pool_state
            WHERE pair_id = ? AND recv_ts_ms >= ?
            ORDER BY block_number DESC, id DESC
            LIMIT ?
            """,
            (pair_id, since_ms, limit),
        ).fetchall()
        ticks = [_row_to_pool_state(r) for r in rows]
        ticks.reverse()
        return ticks

    def rfq_quotes(
        self,
        pair_id: str,
        *,
        since_ms: int = 0,
        limit: int = 500,
    ) -> list[FluxionRfqQuoteTick]:
        rows = self._conn.execute(
            """
            SELECT * FROM fluxion_rfq_quotes
            WHERE pair_id = ? AND poll_ts_ms >= ?
            ORDER BY poll_ts_ms DESC, id DESC
            LIMIT ?
            """,
            (pair_id, since_ms, limit),
        ).fetchall()
        ticks = [_row_to_rfq_quote(r) for r in rows]
        ticks.reverse()
        return ticks

    def swaps(
        self,
        pair_id: str,
        *,
        since_ms: int = 0,
        limit: int = 100,
    ) -> list[FluxionSwapTick]:
        rows = self._conn.execute(
            """
            SELECT * FROM fluxion_swaps
            WHERE pair_id = ? AND recv_ts_ms >= ?
            ORDER BY block_number DESC, log_index DESC, id DESC
            LIMIT ?
            """,
            (pair_id, since_ms, limit),
        ).fetchall()
        ticks = [_row_to_swap(r) for r in rows]
        ticks.reverse()
        return ticks

    def volume_stats(self, pair_id: str, *, since_ms: int) -> VolumeStats:
        """Overview 24h volume proxy — Bybit notional plus Fluxion print count.

        Bybit notional = Σ(price_de_multiplied × size). Fluxion is a count only:
        sizing the USDC leg needs the pool's token order, which lives in M5's
        ``swap_notional_usd`` path — SQL must not invent a pseudo-USD notional.

        Prefer ``latest_cex_volume`` + ``aggregate_dex_volume`` (WHI-777) for
        the panel's CEX/DEX columns; this method remains for TUI legacy cells.
        """
        bt = self._conn.execute(
            """
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(CAST(price_de_multiplied AS REAL)
                                * CAST(size AS REAL)), 0) AS notional
            FROM bybit_trades
            WHERE pair_id = ? AND exchange_ts_ms >= ?
            """,
            (pair_id, since_ms),
        ).fetchone()
        sw = self._conn.execute(
            """
            SELECT COUNT(*) AS n FROM fluxion_swaps
            WHERE pair_id = ? AND recv_ts_ms >= ?
            """,
            (pair_id, since_ms),
        ).fetchone()
        return VolumeStats(
            bybit_trade_count=int(bt["n"]),
            bybit_notional=Decimal(str(bt["notional"])),
            fluxion_swap_count=int(sw["n"]),
        )

    def _table_names(self) -> frozenset[str]:
        if self._tables is None:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            self._tables = frozenset(str(r[0]) for r in rows)
        return self._tables

    def latest_cex_volume(self, pair_id: str) -> CexVolumeTick | None:
        """Most recent REST-polled CEX 24h volume for a pair (WHI-777).

        Returns None when the journal predates schema v5 (table absent) so the
        API can keep serving until the collector restarts and migrates.
        """
        if "cex_volume_24h" not in self._table_names():
            return None
        row = self._conn.execute(
            """
            SELECT * FROM cex_volume_24h
            WHERE pair_id = ?
            ORDER BY poll_ts_ms DESC, id DESC
            LIMIT 1
            """,
            (pair_id,),
        ).fetchone()
        return None if row is None else _row_to_cex_volume(row)

    def earliest_swap_recv_ts_ms(self, pair_id: str) -> int | None:
        """Oldest swap wall-clock for truncation labels (WHI-777)."""
        row = self._conn.execute(
            """
            SELECT MIN(recv_ts_ms) AS ts FROM fluxion_swaps
            WHERE pair_id = ?
            """,
            (pair_id,),
        ).fetchone()
        if row is None or row["ts"] is None:
            return None
        return int(row["ts"])

    def swaps_since(self, pair_id: str, *, since_ms: int) -> list[FluxionSwapTick]:
        """All swaps for a pair with ``recv_ts_ms >= since_ms`` (ascending)."""
        rows = self._conn.execute(
            """
            SELECT * FROM fluxion_swaps
            WHERE pair_id = ? AND recv_ts_ms >= ?
            ORDER BY block_number ASC, log_index ASC, id ASC
            """,
            (pair_id, since_ms),
        ).fetchall()
        return [_row_to_swap(r) for r in rows]

    def trades_since(self, pair_id: str, *, since_ms: int) -> list[BybitTradeTick]:
        """CEX journal trades for session-split secondary volume (WHI-777)."""
        rows = self._conn.execute(
            """
            SELECT * FROM bybit_trades
            WHERE pair_id = ? AND exchange_ts_ms >= ?
            ORDER BY exchange_ts_ms ASC, id ASC
            """,
            (pair_id, since_ms),
        ).fetchall()
        return [_row_to_bybit_trade(r) for r in rows]

    # --- health / meta (WHI-757 web API) ------------------------------------

    def get_meta(self, key: str) -> str | None:
        """Read a ``meta`` key written by the collector (e.g. last_block)."""
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?",
            (key,),
        ).fetchone()
        return None if row is None else str(row["value"])

    def freshest_recv_ts_ms(self) -> int | None:
        """Max wall-clock recv timestamp across live tick tables.

        Used by /api/health as a collector-alive proxy (any feed progressing).
        """
        # Each sub-select is cheap with indexes on recv / poll / exchange ts.
        row = self._conn.execute(
            """
            SELECT MAX(ts) AS ts FROM (
                SELECT MAX(recv_ts_ms) AS ts FROM bybit_book
                UNION ALL
                SELECT MAX(recv_ts_ms) AS ts FROM bybit_trades
                UNION ALL
                SELECT MAX(recv_ts_ms) AS ts FROM fluxion_pool_state
                UNION ALL
                SELECT MAX(recv_ts_ms) AS ts FROM fluxion_swaps
                UNION ALL
                SELECT MAX(recv_ts_ms) AS ts FROM fluxion_rfq_quotes
            )
            """
        ).fetchone()
        if row is None or row["ts"] is None:
            return None
        return int(row["ts"])

    def recent_gaps(self, *, since_ms: int, limit: int = 20) -> list[CollectorGap]:
        """Gaps whose end falls on or after ``since_ms``, newest first."""
        rows = self._conn.execute(
            """
            SELECT source, gap_start_ms, gap_end_ms, detail
            FROM collector_gaps
            WHERE gap_end_ms >= ?
            ORDER BY gap_end_ms DESC, id DESC
            LIMIT ?
            """,
            (since_ms, limit),
        ).fetchall()
        return [
            CollectorGap(
                source=str(r["source"]),
                gap_start_ms=int(r["gap_start_ms"]),
                gap_end_ms=int(r["gap_end_ms"]),
                detail=str(r["detail"]),
            )
            for r in rows
        ]

    def recent_swaps(self, *, limit: int = 50_000) -> list[FluxionSwapTick]:
        """Newest-first swaps for attribution refresh (WHI-768)."""
        rows = self._conn.execute(
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
        return [_row_to_swap(r) for r in rows]

    def recent_rfq_fills(self, *, limit: int = 50_000) -> list[FluxionRfqFillTick]:
        """Newest-first RFQ fills (enriched when WHI-768 columns present)."""
        rows = self._conn.execute(
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
        return [_row_to_rfq_fill(r) for r in rows]

    def recent_erc20_transfers(self, *, limit: int = 100_000) -> list[Erc20TransferTick]:
        """Newest-first native xStock transfers (WHI-768)."""
        rows = self._conn.execute(
            """
            SELECT pair_id, token, block_number, block_ts, recv_ts_ms, tx_hash, log_index,
                   frm, to_addr, amount, amount_raw, gap
            FROM erc20_transfers
            ORDER BY block_number DESC, log_index DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_transfer(r) for r in rows]

    def address_labels(self, *, label: str | None = None) -> list[AddressLabelRow]:
        """All persisted address labels (WHI-768); optional label filter."""
        clauses: list[str] = []
        params: list[object] = []
        if label is not None:
            clauses.append("label = ?")
            params.append(label)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""
            SELECT address, label, evidence_summary, first_seen_ms, last_seen_ms,
                   source, is_rebalancer, n_rfq_maker, n_amm, cex_touch_transfers,
                   updated_at_ms
            FROM address_labels
            {where}
            ORDER BY (last_seen_ms IS NULL), last_seen_ms DESC, address
            """,
            params,
        ).fetchall()
        return [_row_to_address_label(r) for r in rows]

    def address_label_count(self) -> int:
        """Total rows in address_labels (0 → refresh never ran / empty)."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM address_labels").fetchone()
        return int(row["n"]) if row is not None else 0

    def rebalance_events(
        self,
        *,
        pair_id: str | None = None,
        address: str | None = None,
        limit: int = 500,
    ) -> list[RebalanceEventRow]:
        """Rebalance timeline (WHI-768), newest first."""
        clauses: list[str] = []
        params: list[object] = []
        if pair_id is not None:
            clauses.append("pair_id = ?")
            params.append(pair_id)
        if address is not None:
            clauses.append("address = ?")
            params.append(address.lower())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._conn.execute(
            f"""
            SELECT address, counterparty, pair_id, token, amount, direction,
                   block_number, block_ts, recv_ts_ms, tx_hash, log_index
            FROM rebalance_events
            {where}
            ORDER BY block_ts DESC, block_number DESC, log_index DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_row_to_rebalance_event(r) for r in rows]


# --- row mappers ----------------------------------------------------------


def _d(value: object) -> Decimal:
    return Decimal(str(value))


def _row_to_bybit_book(row: sqlite3.Row) -> BybitBookTick:
    return BybitBookTick(
        pair_id=str(row["pair_id"]),
        symbol=str(row["symbol"]),
        exchange_ts_ms=int(row["exchange_ts_ms"]),
        recv_ts_ms=int(row["recv_ts_ms"]),
        bid=_d(row["bid"]),
        ask=_d(row["ask"]),
        bid_de_multiplied=_d(row["bid_de_multiplied"]),
        ask_de_multiplied=_d(row["ask_de_multiplied"]),
        multiplier=_d(row["multiplier"]),
        gap=bool(row["gap"]),
    )


def _row_to_bybit_trade(row: sqlite3.Row) -> BybitTradeTick:
    side = str(row["side"])
    if side not in ("Buy", "Sell"):
        raise ValueError(f"invalid bybit_trades.side={side!r}")
    return BybitTradeTick(
        pair_id=str(row["pair_id"]),
        symbol=str(row["symbol"]),
        exchange_ts_ms=int(row["exchange_ts_ms"]),
        recv_ts_ms=int(row["recv_ts_ms"]),
        trade_id=str(row["trade_id"]),
        price=_d(row["price"]),
        price_de_multiplied=_d(row["price_de_multiplied"]),
        size=_d(row["size"]),
        side=side,  # type: ignore[arg-type]
        multiplier=_d(row["multiplier"]),
        gap=bool(row["gap"]),
    )


def _row_to_cex_volume(row: sqlite3.Row) -> CexVolumeTick:
    src = str(row["source"])
    if src not in ("bybit", "binance"):
        raise ValueError(f"invalid cex_volume_24h.source={src!r}")
    count_raw = row["trade_count_24h"]
    return CexVolumeTick(
        pair_id=str(row["pair_id"]),
        symbol=str(row["symbol"]),
        poll_ts_ms=int(row["poll_ts_ms"]),
        recv_ts_ms=int(row["recv_ts_ms"]),
        volume_quote_24h=_d(row["volume_quote_24h"]),
        trade_count_24h=None if count_raw is None else int(count_raw),
        source=src,  # type: ignore[arg-type]
        gap=bool(row["gap"]),
    )


def _json_decimal_list(raw: object) -> tuple[Decimal, ...]:
    items = json.loads(str(raw))
    if not isinstance(items, list):
        raise ValueError("expected JSON array")
    return tuple(Decimal(str(x)) for x in items)


def _json_optional_decimal_list(raw: object) -> tuple[Decimal | None, ...]:
    items = json.loads(str(raw))
    if not isinstance(items, list):
        raise ValueError("expected JSON array")
    out: list[Decimal | None] = []
    for x in items:
        if x is None:
            out.append(None)
        else:
            out.append(Decimal(str(x)))
    return tuple(out)


def _row_to_bybit_depth(row: sqlite3.Row) -> BybitDepthTick:
    return BybitDepthTick(
        pair_id=str(row["pair_id"]),
        symbol=str(row["symbol"]),
        exchange_ts_ms=int(row["exchange_ts_ms"]),
        recv_ts_ms=int(row["recv_ts_ms"]),
        bid=_d(row["bid"]),
        ask=_d(row["ask"]),
        bid_de_multiplied=_d(row["bid_de_multiplied"]),
        ask_de_multiplied=_d(row["ask_de_multiplied"]),
        multiplier=_d(row["multiplier"]),
        depth_levels=int(row["depth_levels"]),
        buckets_usd=_json_decimal_list(row["buckets_usd"]),
        bid_vwap_dm=_json_optional_decimal_list(row["bid_vwap_dm"]),
        ask_vwap_dm=_json_optional_decimal_list(row["ask_vwap_dm"]),
        gap=bool(row["gap"]),
    )


def _row_to_pool_state(row: sqlite3.Row) -> FluxionPoolStateTick:
    return FluxionPoolStateTick(
        pair_id=str(row["pair_id"]),
        pool=str(row["pool"]),
        block_number=int(row["block_number"]),
        block_ts=int(row["block_ts"]),
        recv_ts_ms=int(row["recv_ts_ms"]),
        sqrt_price_x96=int(row["sqrt_price_x96"]),
        tick=int(row["tick"]),
        liquidity=int(row["liquidity"]),
        token0=str(row["token0"]),
        token1=str(row["token1"]),
        mid_usdc_per_wrapper=_d(row["mid_usdc_per_wrapper"]),
        mid_usdc_per_native=_d(row["mid_usdc_per_native"]),
        wrapper_assets_per_share=_d(row["wrapper_assets_per_share"]),
        gap=bool(row["gap"]),
    )


def _row_to_rfq_quote(row: sqlite3.Row) -> FluxionRfqQuoteTick:
    price_raw = row["price"]
    return FluxionRfqQuoteTick(
        pair_id=str(row["pair_id"]),
        poll_ts_ms=int(row["poll_ts_ms"]),
        recv_ts_ms=int(row["recv_ts_ms"]),
        token_in=str(row["token_in"]),
        token_out=str(row["token_out"]),
        amount_in=str(row["amount_in"]),
        amount_out=None if row["amount_out"] is None else str(row["amount_out"]),
        price=None if price_raw is None else _d(price_raw),
        side=None if row["side"] is None else str(row["side"]),
        request_id=None if row["request_id"] is None else str(row["request_id"]),
        http_status=int(row["http_status"]),
        available=bool(row["available"]),
        gap=bool(row["gap"]),
    )


def _row_to_swap(row: sqlite3.Row) -> FluxionSwapTick:
    price_raw = row["price_usdc_per_wrapper"]
    return FluxionSwapTick(
        pair_id=str(row["pair_id"]),
        pool=str(row["pool"]),
        block_number=int(row["block_number"]),
        block_ts=int(row["block_ts"]),
        recv_ts_ms=int(row["recv_ts_ms"]),
        tx_hash=str(row["tx_hash"]),
        log_index=int(row["log_index"]),
        sender=str(row["sender"]),
        recipient=str(row["recipient"]),
        amount0=int(row["amount0"]),
        amount1=int(row["amount1"]),
        sqrt_price_x96=int(row["sqrt_price_x96"]),
        liquidity=int(row["liquidity"]),
        tick=int(row["tick"]),
        amount_token0=_d(row["amount_token0"]),
        amount_token1=_d(row["amount_token1"]),
        direction=str(row["direction"]),  # type: ignore[arg-type]
        price_usdc_per_wrapper=None if price_raw is None else _d(price_raw),
        gas_used=None if row["gas_used"] is None else int(row["gas_used"]),
        effective_gas_price=(
            None
            if row["effective_gas_price"] is None
            else int(row["effective_gas_price"])
        ),
        gap=bool(row["gap"]),
    )


def _row_to_rfq_fill(row: sqlite3.Row) -> FluxionRfqFillTick:
    keys = set(row.keys())

    def _opt(name: str) -> str | None:
        if name not in keys:
            return None
        val = row[name]
        return None if val is None else str(val)

    enriched = 0
    if "enriched" in keys and row["enriched"] is not None:
        enriched = int(row["enriched"])
    return FluxionRfqFillTick(
        block_number=int(row["block_number"]),
        block_ts=int(row["block_ts"]),
        recv_ts_ms=int(row["recv_ts_ms"]),
        tx_hash=str(row["tx_hash"]),
        log_index=int(row["log_index"]),
        order_hash=str(row["order_hash"]),
        remaining_making_amount=int(str(row["remaining_making_amount"])),
        gap=bool(int(row["gap"] or 0)),
        pair_id=_opt("pair_id"),
        maker=_opt("maker"),
        taker=_opt("taker"),
        direction=_opt("direction"),
        making_token=_opt("making_token"),
        taking_token=_opt("taking_token"),
        making_amount=_opt("making_amount"),
        taking_amount=_opt("taking_amount"),
        usdc_amount=_opt("usdc_amount"),
        stock_amount=_opt("stock_amount"),
        enriched=bool(enriched),
    )


def _row_to_transfer(row: sqlite3.Row) -> Erc20TransferTick:
    return Erc20TransferTick(
        pair_id=str(row["pair_id"]),
        token=str(row["token"]),
        block_number=int(row["block_number"]),
        block_ts=int(row["block_ts"]),
        recv_ts_ms=int(row["recv_ts_ms"]),
        tx_hash=str(row["tx_hash"]),
        log_index=int(row["log_index"]),
        frm=str(row["frm"]),
        to_addr=str(row["to_addr"]),
        amount=_d(row["amount"]),
        amount_raw=int(str(row["amount_raw"])),
        gap=bool(int(row["gap"] or 0)),
    )


def _row_to_address_label(row: sqlite3.Row) -> AddressLabelRow:
    first = row["first_seen_ms"]
    last = row["last_seen_ms"]
    return AddressLabelRow(
        address=str(row["address"]).lower(),
        label=str(row["label"]),
        evidence_summary=str(row["evidence_summary"]),
        first_seen_ms=None if first is None else int(first),
        last_seen_ms=None if last is None else int(last),
        source=str(row["source"]),
        is_rebalancer=bool(int(row["is_rebalancer"] or 0)),
        n_rfq_maker=int(row["n_rfq_maker"] or 0),
        n_amm=int(row["n_amm"] or 0),
        cex_touch_transfers=int(row["cex_touch_transfers"] or 0),
        updated_at_ms=int(row["updated_at_ms"]),
    )


def _row_to_rebalance_event(row: sqlite3.Row) -> RebalanceEventRow:
    return RebalanceEventRow(
        address=str(row["address"]).lower(),
        counterparty=str(row["counterparty"]).lower(),
        pair_id=str(row["pair_id"]),
        token=str(row["token"]).lower(),
        amount=_d(row["amount"]),
        direction=str(row["direction"]),
        block_number=int(row["block_number"]),
        block_ts=int(row["block_ts"]),
        recv_ts_ms=int(row["recv_ts_ms"]),
        tx_hash=str(row["tx_hash"]).lower(),
        log_index=int(row["log_index"]),
    )
