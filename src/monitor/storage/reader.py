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
    FluxionPoolStateTick,
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
