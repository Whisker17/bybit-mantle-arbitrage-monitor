"""Per-pair withdrawal fee params for PnL/edge (WHI-961).

Single seam for inventory → engine: API, TUI, and CLI call
``withdrawal_params_from_pair`` so pair fee + multiplier stay consistent.
"""

from __future__ import annotations

from decimal import Decimal

from monitor.metrics.edge import Direction, WithdrawalFeeParams
from monitor.symbols.bstocks_models import BStocksPair
from monitor.symbols.models import Pair


def withdrawal_params_from_pair(pair: Pair | BStocksPair) -> WithdrawalFeeParams:
    """Map inventory pair → withdrawal fee schedule.

    * Bybit/Fluxion ``Pair``: measured ``asset_withdrawal_fee_tokens`` (may be
      None) + ``bybit.multiplier``.
    * Binance/Pancake ``BStocksPair``: out of scope (no measured schedule) →
      unknown dir2 annotation via ``asset_fee_tokens=None``.
    """
    if isinstance(pair, BStocksPair):
        return WithdrawalFeeParams(asset_fee_tokens=None, price_multiplier=Decimal(1))
    return WithdrawalFeeParams(
        asset_fee_tokens=pair.asset_withdrawal_fee_tokens,
        price_multiplier=pair.bybit.multiplier,
    )


def is_unpriced_dir2(
    direction: Direction, asset_fee_tokens: Decimal | None
) -> bool:
    """True when dir2 cannot be ranked because the token fee is unmeasured.

    Shared by PnL v2 best-of and capture sample construction (WHI-1090).
    Dir1 always has the stable schedule. A computed ``unknown`` kind from a
    non-positive listed mid is a degenerate quote, not a missing inventory fee.
    """
    return direction == "buy_bybit_sell_fluxion" and asset_fee_tokens is None


__all__ = [
    "WithdrawalFeeParams",
    "is_unpriced_dir2",
    "withdrawal_params_from_pair",
]
