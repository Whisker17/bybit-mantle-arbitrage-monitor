"""Per-pair withdrawal fee params for PnL/edge (WHI-961).

Single seam for inventory → engine: both API and TUI call
``withdrawal_params_from_pair`` so pair fee + multiplier stay consistent.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from monitor.metrics.edge import WithdrawalFeeParams
from monitor.symbols.bstocks_models import BStocksPair
from monitor.symbols.models import Pair


def withdrawal_params_from_pair(pair: Any) -> WithdrawalFeeParams:
    """Map inventory pair → withdrawal fee schedule.

    * Bybit/Fluxion ``Pair``: measured ``asset_withdrawal_fee_tokens`` (may be
      None) + ``bybit.multiplier``.
    * Binance/Pancake ``BStocksPair``: out of scope (no measured schedule) →
      unknown dir2 annotation via ``asset_fee_tokens=None``.
    """
    if isinstance(pair, BStocksPair):
        return WithdrawalFeeParams(asset_fee_tokens=None, price_multiplier=Decimal(1))
    if isinstance(pair, Pair):
        return WithdrawalFeeParams(
            asset_fee_tokens=pair.asset_withdrawal_fee_tokens,
            price_multiplier=pair.bybit.multiplier,
        )
    # Unknown inventory shape — degrade to unknown, never invent a fee.
    return WithdrawalFeeParams(asset_fee_tokens=None, price_multiplier=Decimal(1))


__all__ = [
    "WithdrawalFeeParams",
    "withdrawal_params_from_pair",
]
