"""Mutable N-level Bybit order book (orderbook.50+) → L1 + depth ticks (WHI-755)."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from monitor.bybit.depth_math import (
    SideMap,
    apply_side_ops,
    best_ask,
    best_bid,
    de_multiplied_levels,
    sorted_ask_levels,
    sorted_bid_levels,
    vwap_curve,
)
from monitor.bybit.parse import parse_orderbook_l1_update
from monitor.quotes import BybitBookTick, BybitDepthTick, now_ms
from monitor.symbols.multipliers import de_multiplied_price

logger = logging.getLogger(__name__)

# PnL v2 AMM buckets (DESIGN §2.6.3) — default when config does not inject.
DEFAULT_DEPTH_BUCKETS_USD: tuple[Decimal, ...] = (
    Decimal("10"),
    Decimal("50"),
    Decimal("100"),
    Decimal("500"),
    Decimal("1000"),
    Decimal("10000"),
)


@dataclass(slots=True)
class _DepthState:
    bids: SideMap = field(default_factory=dict)
    asks: SideMap = field(default_factory=dict)
    last_u: int | None = None
    last_seq: int | None = None
    established: bool = False
    pending_gap: bool = False


@dataclass(frozen=True, slots=True)
class DepthApplyResult:
    """One accepted book update: always L1 when two-sided; depth when requested."""

    book: BybitBookTick
    depth: BybitDepthTick | None


class DepthBookTracker:
    """Per-symbol multi-level book: snapshot/delta merge, drop non-increasing u/seq.

    Emits L1 ``BybitBookTick`` (same shape as the orderbook.1 path) plus an optional
    ``BybitDepthTick`` with precomputed de-multiplied VWAP at configured USD buckets.
    """

    def __init__(
        self,
        *,
        pair_id_by_symbol: Mapping[str, str],
        multiplier_by_symbol: Mapping[str, Decimal],
        buckets_usd: Sequence[Decimal] | None = None,
        emit_depth: bool = True,
    ) -> None:
        self._pair_id_by_symbol = dict(pair_id_by_symbol)
        self._multiplier_by_symbol = dict(multiplier_by_symbol)
        raw = list(buckets_usd) if buckets_usd is not None else list(DEFAULT_DEPTH_BUCKETS_USD)
        if not raw:
            raise ValueError("buckets_usd must be non-empty when depth is used")
        for q in raw:
            if q <= 0:
                raise ValueError(f"bucket must be > 0, got {q}")
        self._buckets_usd: tuple[Decimal, ...] = tuple(raw)
        self._emit_depth = emit_depth
        self._states: dict[str, _DepthState] = {}

    @property
    def buckets_usd(self) -> tuple[Decimal, ...]:
        return self._buckets_usd

    def apply(
        self,
        payload: dict[str, Any],
        *,
        recv_ts_ms: int | None = None,
        gap: bool = False,
    ) -> DepthApplyResult | None:
        update = parse_orderbook_l1_update(payload)
        if update is None:
            return None
        pair_id = self._pair_id_by_symbol.get(update.symbol)
        mult = self._multiplier_by_symbol.get(update.symbol)
        if pair_id is None or mult is None:
            return None

        state = self._states.get(update.symbol)
        if state is None:
            state = _DepthState()
            self._states[update.symbol] = state

        is_snapshot = update.msg_type == "snapshot"
        if not is_snapshot and not state.established:
            logger.debug("bybit depth drop orphan delta symbol=%s", update.symbol)
            return None
        if not is_snapshot and not self._is_in_order(state, update.u, update.seq):
            logger.debug(
                "bybit depth drop stale update symbol=%s u=%s last_u=%s",
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
            state.pending_gap = True
            logger.debug(
                "bybit depth u gap symbol=%s last_u=%s u=%s",
                update.symbol,
                state.last_u,
                update.u,
            )

        state.bids = apply_side_ops(
            state.bids, update.bid_ops, is_snapshot=is_snapshot
        )
        state.asks = apply_side_ops(
            state.asks, update.ask_ops, is_snapshot=is_snapshot
        )

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

        bid = best_bid(state.bids)
        ask = best_ask(state.asks)
        if bid is None or ask is None:
            return None
        if bid <= 0 or ask <= 0:
            return None
        if bid >= ask:
            logger.debug(
                "bybit depth drop crossed book symbol=%s bid=%s ask=%s",
                update.symbol,
                bid,
                ask,
            )
            return None

        recv = recv_ts_ms if recv_ts_ms is not None else now_ms()
        exchange_ts = update.exchange_ts_ms if update.exchange_ts_ms is not None else recv
        tick_gap = gap or state.pending_gap
        state.pending_gap = False

        book = BybitBookTick(
            pair_id=pair_id,
            symbol=update.symbol,
            exchange_ts_ms=exchange_ts,
            recv_ts_ms=recv,
            bid=bid,
            ask=ask,
            bid_de_multiplied=de_multiplied_price(bid, mult),
            ask_de_multiplied=de_multiplied_price(ask, mult),
            multiplier=mult,
            gap=tick_gap,
        )

        depth: BybitDepthTick | None = None
        if self._emit_depth:
            bid_lv_dm = de_multiplied_levels(sorted_bid_levels(state.bids), mult)
            ask_lv_dm = de_multiplied_levels(sorted_ask_levels(state.asks), mult)
            depth = BybitDepthTick(
                pair_id=pair_id,
                symbol=update.symbol,
                exchange_ts_ms=exchange_ts,
                recv_ts_ms=recv,
                bid=bid,
                ask=ask,
                bid_de_multiplied=book.bid_de_multiplied,
                ask_de_multiplied=book.ask_de_multiplied,
                multiplier=mult,
                depth_levels=max(len(bid_lv_dm), len(ask_lv_dm)),
                buckets_usd=self._buckets_usd,
                bid_vwap_dm=tuple(vwap_curve(bid_lv_dm, list(self._buckets_usd))),
                ask_vwap_dm=tuple(vwap_curve(ask_lv_dm, list(self._buckets_usd))),
                gap=tick_gap,
            )

        return DepthApplyResult(book=book, depth=depth)

    @staticmethod
    def _is_in_order(state: _DepthState, u: int | None, seq: int | None) -> bool:
        if u is not None and state.last_u is not None:
            return u > state.last_u
        if seq is not None and state.last_seq is not None:
            return seq > state.last_seq
        return True
