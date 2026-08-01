"""Mutable per-symbol L1 book state for Bybit orderbook.1 (collector-side)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from monitor.bybit.parse import apply_l1_side, parse_orderbook_l1_update
from monitor.quotes import BybitBookTick, now_ms
from monitor.symbols.multipliers import de_multiplied_price

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _L1State:
    bid: Decimal | None = None
    ask: Decimal | None = None
    last_u: int | None = None
    last_seq: int | None = None
    established: bool = False  # True after first accepted snapshot
    pending_gap: bool = False  # set when u jumps (message loss)


class L1BookTracker:
    """Per-symbol L1 book: merge snapshot/delta, drop non-increasing ``u``/``seq``.

    Lives next to the collector (not in pure parse). Resync only via snapshot;
    orphan deltas before the first snapshot are ignored.
    """

    def __init__(
        self,
        *,
        pair_id_by_symbol: Mapping[str, str],
        multiplier_by_symbol: Mapping[str, Decimal],
    ) -> None:
        self._pair_id_by_symbol = dict(pair_id_by_symbol)
        self._multiplier_by_symbol = dict(multiplier_by_symbol)
        self._states: dict[str, _L1State] = {}

    def apply(
        self,
        payload: dict[str, Any],
        *,
        recv_ts_ms: int | None = None,
        gap: bool = False,
    ) -> BybitBookTick | None:
        update = parse_orderbook_l1_update(payload)
        if update is None:
            return None
        pair_id = self._pair_id_by_symbol.get(update.symbol)
        mult = self._multiplier_by_symbol.get(update.symbol)
        if pair_id is None or mult is None:
            return None

        state = self._states.get(update.symbol)
        if state is None:
            state = _L1State()
            self._states[update.symbol] = state

        is_snapshot = update.msg_type == "snapshot"
        if not is_snapshot and not state.established:
            logger.debug("bybit l1 drop orphan delta symbol=%s", update.symbol)
            return None
        if not is_snapshot and not self._is_in_order(state, update.u, update.seq):
            logger.debug(
                "bybit l1 drop stale update symbol=%s u=%s last_u=%s",
                update.symbol,
                update.u,
                state.last_u,
            )
            return None

        if (
            not is_snapshot
            and update.u is not None
            and state.last_u is not None
            and update.u > state.last_u + 1
        ):
            # Non-contiguous u ⇒ missed delta(s); next emitted tick is gapped.
            state.pending_gap = True
            logger.debug(
                "bybit l1 u gap symbol=%s last_u=%s u=%s",
                update.symbol,
                state.last_u,
                update.u,
            )

        state.bid = apply_l1_side(
            state.bid,
            update.bid_ops,
            is_snapshot=is_snapshot,
            prefer_high=True,
        )
        state.ask = apply_l1_side(
            state.ask,
            update.ask_ops,
            is_snapshot=is_snapshot,
            prefer_high=False,
        )

        # Snapshot always rewrites sequence watermarks (even to None).
        if is_snapshot:
            state.last_u = update.u
            state.last_seq = update.seq
            state.established = True
            state.pending_gap = False
        else:
            if update.u is not None:
                state.last_u = update.u
            if update.seq is not None:
                state.last_seq = update.seq

        if state.bid is None or state.ask is None:
            return None
        if state.bid <= 0 or state.ask <= 0:
            return None
        # Independent side merges can briefly cross; never emit unusable L1.
        if state.bid >= state.ask:
            logger.debug(
                "bybit l1 drop crossed book symbol=%s bid=%s ask=%s",
                update.symbol,
                state.bid,
                state.ask,
            )
            return None

        recv = recv_ts_ms if recv_ts_ms is not None else now_ms()
        exchange_ts = update.exchange_ts_ms if update.exchange_ts_ms is not None else recv
        tick_gap = gap or state.pending_gap
        state.pending_gap = False
        return BybitBookTick(
            pair_id=pair_id,
            symbol=update.symbol,
            exchange_ts_ms=exchange_ts,
            recv_ts_ms=recv,
            bid=state.bid,
            ask=state.ask,
            bid_de_multiplied=de_multiplied_price(state.bid, mult),
            ask_de_multiplied=de_multiplied_price(state.ask, mult),
            multiplier=mult,
            gap=tick_gap,
        )

    @staticmethod
    def _is_in_order(state: _L1State, u: int | None, seq: int | None) -> bool:
        """Accept delta only when ``u`` (prefer) or ``seq`` is strictly increasing."""
        if u is not None and state.last_u is not None:
            return u > state.last_u
        if seq is not None and state.last_seq is not None:
            return seq > state.last_seq
        return True
