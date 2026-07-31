"""Bybit xStock multiplier application (DESIGN §2.1 / §2.2).

Bybit exposes ``xstockMultiplier`` on spot instruments-info. Token mid must be
**de-multiplied** before comparing to Fluxion native xStock prices:

    comparable = bybit_token_price / multiplier

Multiplier is a positive Decimal; a value of 1 means no adjustment.
"""

from __future__ import annotations

from decimal import Decimal

from monitor.symbols.models import PairsConfig


def de_multiplied_price(price: Decimal | str, multiplier: Decimal | str) -> Decimal:
    """Return price / multiplier for Bybit → Fluxion comparison."""
    p = Decimal(str(price))
    m = Decimal(str(multiplier))
    if m <= 0:
        raise ValueError(f"multiplier must be > 0, got {m}")
    return p / m


def multiplier_map(config: PairsConfig) -> dict[str, Decimal]:
    """Map pair id → Bybit multiplier from the loaded inventory."""
    return {pair.id: pair.bybit.multiplier for pair in config.pairs}
