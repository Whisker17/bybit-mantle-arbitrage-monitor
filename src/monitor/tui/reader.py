"""Read-only SQLite access for the TUI (collector journal → quote ticks).

Opens a separate connection so the collector process can keep writing under WAL.
All methods reconstruct ``monitor.quotes`` shapes so builders call M3/M4 pure
seams without re-deriving formulas.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from monitor.metrics.edge import mid_from_bid_ask
from monitor.quotes import (
    BybitBookTick,
    FluxionPoolStateTick,
    FluxionRfqFillTick,
    FluxionRfqQuoteTick,
    FluxionSwapTick,
)


@dataclass(frozen=True, slots=True)
class VolumeStats:
    """Rolling window volume / print counts for one pair."""

    bybit_trade_count: int
    bybit_notional: Decimal  # sum(price_de_multiplied * size) in quote units
    fluxion_swap_count: int
    fluxion_notional_usd: Decimal  # sum(abs(USDC leg)) when known from amounts


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
        self, pair_id: str, *, side: str | None = None
    ) -> FluxionRfqQuoteTick | None:
        if side is None:
            row = self._conn.execute(
                """
                SELECT * FROM fluxion_rfq_quotes
                WHERE pair_id = ?
                ORDER BY poll_ts_ms DESC, id DESC
                LIMIT 1
                """,
                (pair_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                """
                SELECT * FROM fluxion_rfq_quotes
                WHERE pair_id = ? AND side = ?
                ORDER BY poll_ts_ms DESC, id DESC
                LIMIT 1
                """,
                (pair_id, side),
            ).fetchone()
        return None if row is None else _row_to_rfq_quote(row)

    def latest_rfq_sides(
        self, pair_id: str
    ) -> tuple[FluxionRfqQuoteTick | None, FluxionRfqQuoteTick | None]:
        """Return (buy_native, sell_native) latest available quotes."""
        buy = self.latest_rfq_quote(pair_id, side="buy_native")
        if buy is None:
            buy = self.latest_rfq_quote(pair_id, side="buy")
        sell = self.latest_rfq_quote(pair_id, side="sell_native")
        if sell is None:
            sell = self.latest_rfq_quote(pair_id, side="sell")
        return buy, sell

    # --- history for sparklines / stats -----------------------------------

    def bybit_books(
        self,
        pair_id: str,
        *,
        since_ms: int | None = None,
        limit: int = 500,
    ) -> list[BybitBookTick]:
        if since_ms is None:
            rows = self._conn.execute(
                """
                SELECT * FROM bybit_book
                WHERE pair_id = ?
                ORDER BY exchange_ts_ms DESC, id DESC
                LIMIT ?
                """,
                (pair_id, limit),
            ).fetchall()
        else:
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
        since_ms: int | None = None,
        limit: int = 500,
    ) -> list[FluxionPoolStateTick]:
        # block_ts is seconds; join window uses recv_ts_ms for wall clock.
        if since_ms is None:
            rows = self._conn.execute(
                """
                SELECT * FROM fluxion_pool_state
                WHERE pair_id = ?
                ORDER BY block_number DESC, id DESC
                LIMIT ?
                """,
                (pair_id, limit),
            ).fetchall()
        else:
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
        since_ms: int | None = None,
        limit: int = 500,
    ) -> list[FluxionRfqQuoteTick]:
        if since_ms is None:
            rows = self._conn.execute(
                """
                SELECT * FROM fluxion_rfq_quotes
                WHERE pair_id = ?
                ORDER BY poll_ts_ms DESC, id DESC
                LIMIT ?
                """,
                (pair_id, limit),
            ).fetchall()
        else:
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
        since_ms: int | None = None,
        limit: int = 100,
    ) -> list[FluxionSwapTick]:
        if since_ms is None:
            rows = self._conn.execute(
                """
                SELECT * FROM fluxion_swaps
                WHERE pair_id = ?
                ORDER BY block_number DESC, log_index DESC, id DESC
                LIMIT ?
                """,
                (pair_id, limit),
            ).fetchall()
        else:
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

    def rfq_fills(
        self,
        *,
        since_ms: int | None = None,
        limit: int = 200,
    ) -> list[FluxionRfqFillTick]:
        if since_ms is None:
            rows = self._conn.execute(
                """
                SELECT * FROM fluxion_rfq_fills
                ORDER BY block_number DESC, log_index DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM fluxion_rfq_fills
                WHERE recv_ts_ms >= ?
                ORDER BY block_number DESC, log_index DESC, id DESC
                LIMIT ?
                """,
                (since_ms, limit),
            ).fetchall()
        ticks = [_row_to_rfq_fill(r) for r in rows]
        ticks.reverse()
        return ticks

    def bybit_mid_series(
        self,
        pair_id: str,
        *,
        since_ms: int | None = None,
        limit: int = 2000,
    ) -> list[tuple[int, Decimal]]:
        """(exchange_ts_ms, mid) ascending for lead-lag joins."""
        books = self.bybit_books(pair_id, since_ms=since_ms, limit=limit)
        out: list[tuple[int, Decimal]] = []
        for b in books:
            if b.bid_de_multiplied > 0 and b.ask_de_multiplied > 0:
                out.append(
                    (b.exchange_ts_ms, mid_from_bid_ask(b.bid_de_multiplied, b.ask_de_multiplied))
                )
        return out

    def volume_stats(self, pair_id: str, *, since_ms: int) -> VolumeStats:
        """Overview 24h volume proxy — not M4 ``swap_notional_usd``.

        Bybit notional = Σ(price_de_multiplied × size). Fluxion notional is a
        coarse SQL max(|amount_token0|, |amount_token1|) count aid only; the
        detail trade stream uses M4 ``swap_notional_usd`` with known token order.
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
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(
                     CASE
                       WHEN ABS(CAST(amount_token0 AS REAL))
                            >= ABS(CAST(amount_token1 AS REAL))
                       THEN ABS(CAST(amount_token0 AS REAL))
                       ELSE ABS(CAST(amount_token1 AS REAL))
                     END
                   ), 0) AS notional
            FROM fluxion_swaps
            WHERE pair_id = ? AND recv_ts_ms >= ?
            """,
            (pair_id, since_ms),
        ).fetchone()
        return VolumeStats(
            bybit_trade_count=int(bt["n"]),
            bybit_notional=Decimal(str(bt["notional"])),
            fluxion_swap_count=int(sw["n"]),
            fluxion_notional_usd=Decimal(str(sw["notional"])),
        )

    def pair_ids_with_data(self) -> list[str]:
        rows = self._conn.execute(
            """
            SELECT pair_id FROM bybit_book
            UNION
            SELECT pair_id FROM fluxion_pool_state
            UNION
            SELECT pair_id FROM fluxion_rfq_quotes
            """
        ).fetchall()
        return sorted({str(r["pair_id"]) for r in rows})


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
    return FluxionRfqFillTick(
        block_number=int(row["block_number"]),
        block_ts=int(row["block_ts"]),
        recv_ts_ms=int(row["recv_ts_ms"]),
        tx_hash=str(row["tx_hash"]),
        log_index=int(row["log_index"]),
        order_hash=str(row["order_hash"]),
        remaining_making_amount=int(row["remaining_making_amount"]),
        gap=bool(row["gap"]),
    )


def downsample(
    points: Sequence[tuple[int, Decimal]], *, max_points: int
) -> list[tuple[int, Decimal]]:
    """Evenly subsample a time series keeping first and last."""
    if max_points < 2 or len(points) <= max_points:
        return list(points)
    n = len(points)
    # Always include endpoints.
    idxs = {0, n - 1}
    for i in range(1, max_points - 1):
        idxs.add(round(i * (n - 1) / (max_points - 1)))
    return [points[i] for i in sorted(idxs)]
