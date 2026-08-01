"""Normalized fill events for attribution (denormalized from monitor.quotes).

Attribution logic depends on these shapes, not on WS/RPC clients or SQLite.
Builders map collector ticks + contemporaneous mids into these events.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from monitor.metrics.session import SessionKind
from monitor.quotes import FluxionRfqFillTick, FluxionSwapTick

SwapDirection = Literal["buy_native", "sell_native"]


@dataclass(frozen=True, slots=True)
class AmmTradeEvent:
    """One AMM swap ready for mechanism=AMM + behavior features."""

    pair_id: str
    ts_ms: int
    block_number: int
    tx_hash: str
    log_index: int
    taker: str
    sender: str
    direction: SwapDirection
    notional_usd: Decimal
    # Pre-swap Fluxion mid (USDC per native); None if unknown.
    fluxion_mid_pre: Decimal | None
    # Contemporaneous de-multiplied Bybit mid; None if unknown.
    bybit_mid: Decimal | None
    # Bybit mid ~lookback_ms earlier (for lead-lag align); None if unknown.
    bybit_mid_prev: Decimal | None
    session: SessionKind


@dataclass(frozen=True, slots=True)
class RfqFillEvent:
    """One RFQ / LOP settlement (mechanism=RFQ).

    ``pair_id`` is optional until fill enrichment lands (DEFERRED_ISSUES).
    ``session`` should be stamped by the producer from fill timestamp so
    session-scoped mechanism share stays coherent with AMM.
    """

    ts_ms: int
    block_number: int
    tx_hash: str
    log_index: int
    pair_id: str | None = None
    order_hash: str | None = None
    session: SessionKind | None = None


def swap_notional_usd(swap: FluxionSwapTick, *, quote_is_token0: bool) -> Decimal:
    """Absolute USDC-leg notional from a decoded swap (pool amount convention)."""
    leg = swap.amount_token0 if quote_is_token0 else swap.amount_token1
    return abs(leg)


def amm_trade_from_swap(
    swap: FluxionSwapTick,
    *,
    notional_usd: Decimal,
    fluxion_mid_pre: Decimal | None,
    bybit_mid: Decimal | None,
    session: SessionKind,
    bybit_mid_prev: Decimal | None = None,
    ts_ms: int | None = None,
) -> AmmTradeEvent | None:
    """Lift a decoded swap tick into an attribution event.

    Returns None when direction is unknown (cannot label behavior).

    Callers (M5 / offline QA) supply contemporaneous mids and session — typical
    join::

        notional = swap_notional_usd(swap, quote_is_token0=...)
        prev = resolve_bybit_mid_prev(ts, bybit_series, lookback_ms=cfg...)
        session = session_kind(datetime.fromtimestamp(ts/1000, tz=UTC), config=metrics_cfg)
        event = amm_trade_from_swap(swap, notional_usd=notional, ...)
    """
    if swap.direction not in ("buy_native", "sell_native"):
        return None
    if notional_usd < 0:
        raise ValueError("notional_usd must be >= 0")
    # UniV3 Swap topics: sender = msg.sender (often a router), recipient = the
    # address the pool pays — the beneficiary we label (phase-1 m6 convention).
    # ``sender`` is retained for offline QA / future role probes.
    taker = swap.recipient.lower()
    sender = swap.sender.lower()
    return AmmTradeEvent(
        pair_id=swap.pair_id,
        ts_ms=ts_ms if ts_ms is not None else swap.recv_ts_ms,
        block_number=swap.block_number,
        tx_hash=swap.tx_hash,
        log_index=swap.log_index,
        taker=taker,
        sender=sender,
        direction=swap.direction,
        notional_usd=notional_usd,
        fluxion_mid_pre=fluxion_mid_pre,
        bybit_mid=bybit_mid,
        bybit_mid_prev=bybit_mid_prev,
        session=session,
    )


def rfq_fill_from_tick(
    tick: FluxionRfqFillTick,
    *,
    pair_id: str | None = None,
    ts_ms: int | None = None,
    session: SessionKind | None = None,
) -> RfqFillEvent:
    return RfqFillEvent(
        ts_ms=ts_ms if ts_ms is not None else tick.recv_ts_ms,
        block_number=tick.block_number,
        tx_hash=tick.tx_hash,
        log_index=tick.log_index,
        pair_id=pair_id,
        order_hash=tick.order_hash,
        session=session,
    )
