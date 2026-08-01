"""Per-trade convergence and Bybit lead-lag alignment features."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from monitor.attribution.events import SwapDirection

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
