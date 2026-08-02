"""CEX multiplier application (DESIGN §2.1 / §2.2; M7-1 bStocks multiply).

Bybit xStocks (``xstockMultiplier``) — **divide** before Fluxion compare:

    comparable = bybit_token_price / multiplier

Binance bStocks (BEP-677 ``uiMultiplier``) — **multiply** for raw/on-chain compare:

    comparable = binance_display_price * ui_multiplier

Both land in the journal's ``*_de_multiplied`` columns (historical name = comparable
price in the AMM's raw-token space). Multiplier is a positive Decimal; 1 means none.
"""

from __future__ import annotations

from decimal import Decimal

from monitor.symbols.bstocks_models import BStocksPairsConfig
from monitor.symbols.models import PairsConfig


def de_multiplied_price(price: Decimal | str, multiplier: Decimal | str) -> Decimal:
    """Return price / multiplier for Bybit → Fluxion comparison."""
    p = Decimal(str(price))
    m = Decimal(str(multiplier))
    if m <= 0:
        raise ValueError(f"multiplier must be > 0, got {m}")
    return p / m


def multiplied_price(price: Decimal | str, ui_multiplier: Decimal | str) -> Decimal:
    """Return price * ui_multiplier for Binance bStocks → Pancake raw compare."""
    p = Decimal(str(price))
    m = Decimal(str(ui_multiplier))
    if m <= 0:
        raise ValueError(f"ui_multiplier must be > 0, got {m}")
    return p * m


def de_multiplied_size(size: Decimal | str, multiplier: Decimal | str) -> Decimal:
    """Bybit: raw size → de-multiplied base so notional = price_dm * size_dm."""
    s = Decimal(str(size))
    m = Decimal(str(multiplier))
    if m <= 0:
        raise ValueError(f"multiplier must be > 0, got {m}")
    return s * m


def multiplied_size(size: Decimal | str, ui_multiplier: Decimal | str) -> Decimal:
    """Binance: display size → raw-space size so notional = price_dm * size_dm.

    With ``price_dm = price * mult`` and ``size_dm = size / mult``, notional is
    unchanged: ``price * size``.
    """
    s = Decimal(str(size))
    m = Decimal(str(ui_multiplier))
    if m <= 0:
        raise ValueError(f"ui_multiplier must be > 0, got {m}")
    return s / m


def multiplier_map(config: PairsConfig) -> dict[str, Decimal]:
    """Map Bybit spot symbol → multiplier (for the Bybit collector boot path)."""
    return {pair.bybit.symbol: pair.bybit.multiplier for pair in config.pairs}


def multiplier_map_by_pair_id(config: PairsConfig) -> dict[str, Decimal]:
    """Map pair id (e.g. TSLAx) → multiplier for panel/metrics keyed by inventory id."""
    return {pair.id: pair.bybit.multiplier for pair in config.pairs}


def ui_multiplier_map(config: BStocksPairsConfig) -> dict[str, Decimal]:
    """Map Binance spot symbol → ui_multiplier (bStocks collector boot path)."""
    return {pair.binance.symbol.upper(): pair.binance.ui_multiplier for pair in config.pairs}


def ui_multiplier_map_by_pair_id(config: BStocksPairsConfig) -> dict[str, Decimal]:
    """Map pair id → ui_multiplier for panel/metrics keyed by inventory id."""
    return {pair.id: pair.binance.ui_multiplier for pair in config.pairs}
