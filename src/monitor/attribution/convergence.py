"""Per-trade convergence and Bybit lead-lag alignment features."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from monitor.attribution.events import AmmTradeEvent, SwapDirection

BPS = Decimal(10_000)


def is_converging(
    direction: SwapDirection,
    *,
    fluxion_mid: Decimal,
    bybit_mid: Decimal,
) -> bool | None:
    """Whether the trade direction moves Fluxion mid toward Bybit mid.

    ``buy_native`` raises the pool mid; ``sell_native`` lowers it.
    Returns None when mids are non-positive or already equal (undefined).
    """
    if fluxion_mid <= 0 or bybit_mid <= 0:
        return None
    if fluxion_mid == bybit_mid:
        return None
    if fluxion_mid > bybit_mid:
        return direction == "sell_native"
    return direction == "buy_native"


def bybit_move_aligned(
    direction: SwapDirection,
    *,
    bybit_mid: Decimal,
    bybit_mid_prev: Decimal,
    min_move_bps: Decimal,
) -> bool | None:
    """Whether trade direction matches the sign of a recent Bybit mid move.

    Arb bots that re-peg Fluxion after Bybit moves buy native when Bybit rose
    (Fluxion lagging rich-to-cheap) and sell when Bybit fell. Returns None when
    the move is below ``min_move_bps`` or mids are non-positive.
    """
    if bybit_mid <= 0 or bybit_mid_prev <= 0:
        return None
    delta = bybit_mid - bybit_mid_prev
    move_bps = abs(delta) / bybit_mid_prev * BPS
    if move_bps < min_move_bps:
        return None
    if delta > 0:
        return direction == "buy_native"
    if delta < 0:
        return direction == "sell_native"
    return None


def trade_convergence_flag(
    direction: SwapDirection | Literal["unknown"],
    fluxion_mid: Decimal | None,
    bybit_mid: Decimal | None,
) -> bool | None:
    """Safe wrapper for event fields that may be missing."""
    if direction not in ("buy_native", "sell_native"):
        return None
    if fluxion_mid is None or bybit_mid is None:
        return None
    return is_converging(direction, fluxion_mid=fluxion_mid, bybit_mid=bybit_mid)


def convergence_share(trades: Sequence[AmmTradeEvent]) -> tuple[float | None, int]:
    """Return (hits/scored, scored_count) for trades with a defined convergence flag."""
    hits = 0
    scored = 0
    for t in trades:
        flag = trade_convergence_flag(t.direction, t.fluxion_mid_pre, t.bybit_mid)
        if flag is None:
            continue
        scored += 1
        if flag:
            hits += 1
    if scored <= 0:
        return None, 0
    return hits / scored, scored


def resolve_bybit_mid_prev(
    trade_ts_ms: int,
    bybit_mids: Sequence[tuple[int, Decimal]],
    *,
    lookback_ms: int,
) -> Decimal | None:
    """Pick the Bybit mid at or before ``trade_ts_ms - lookback_ms``.

    ``bybit_mids`` is a sequence of ``(ts_ms, mid)`` samples, any order.
    Returns None when no sample is at or before the lookback anchor.
    """
    if lookback_ms < 1:
        raise ValueError("lookback_ms must be >= 1")
    anchor = trade_ts_ms - lookback_ms
    best_ts: int | None = None
    best_mid: Decimal | None = None
    for ts, mid in bybit_mids:
        if ts > anchor:
            continue
        if best_ts is None or ts > best_ts:
            best_ts = ts
            best_mid = mid
    return best_mid
