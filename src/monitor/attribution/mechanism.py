"""Mechanism layer: RFQ (MM-driven) vs AMM (active taker)."""

from __future__ import annotations

from enum import StrEnum

from monitor.attribution.events import AmmTradeEvent, RfqFillEvent
from monitor.quotes import FluxionRfqFillTick, FluxionSwapTick


class Mechanism(StrEnum):
    """How the fill was executed — orthogonal to behavior labels."""

    AMM = "amm"
    RFQ = "rfq"


def mechanism_for_swap(_swap: FluxionSwapTick | AmmTradeEvent) -> Mechanism:
    """AMM pool swaps are always active-taker mechanism."""
    return Mechanism.AMM


def mechanism_for_rfq_fill(_fill: FluxionRfqFillTick | RfqFillEvent) -> Mechanism:
    """RFQ / LOP settlements are MM quote-driven."""
    return Mechanism.RFQ
