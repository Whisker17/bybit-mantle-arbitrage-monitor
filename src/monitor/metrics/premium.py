"""Tokenized mid vs underlying equity premium (WHI-779).

Premium is measured in **bps of the underlying**:

    premium_bps = (de_multiplied_tokenized_mid / underlying_price − 1) × 10⁴

``de_multiplied`` is the journal comparable price (Bybit: divide xstockMultiplier;
Binance: multiply uiMultiplier). AMM ``mid_usdc_per_native`` and RFQ prices are
already per-share USDC/USDT space after collection.

Closed-session semantics live on ``UnderlyingPriceTick.price_type`` — never treat
``close`` / ``pre`` / ``post`` as a silent live premium; UI labels via
``premium_type_label``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from monitor.quotes import UnderlyingPriceTick

PriceType = Literal["live", "pre", "post", "close", "stale"]
EmptyReason = Literal["no_data", "private"]

_BPS = Decimal(10_000)

_TYPE_LABELS: dict[str, str] = {
    "live": "live",
    "pre": "vs pre",
    "post": "vs post",
    "close": "vs close",
    "stale": "stale",
}


def premium_bps(
    tokenized_mid: Decimal | None,
    underlying: Decimal | None,
) -> Decimal | None:
    """Return (tokenized / underlying − 1) in bps, or None when undefined."""
    if tokenized_mid is None or underlying is None:
        return None
    if tokenized_mid <= 0 or underlying <= 0:
        return None
    return (tokenized_mid / underlying - Decimal(1)) * _BPS


def mean_mid(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    """Mean of available mids (RFQ buy/sell → single RFQ mid for premium)."""
    sides = [x for x in (a, b) if x is not None and x > 0]
    if not sides:
        return None
    if len(sides) == 1:
        return sides[0]
    return (sides[0] + sides[1]) / 2


def premium_type_label(price_type: str | None) -> str | None:
    """Human annotation for the premium column (never silent on close/pre/post)."""
    if price_type is None:
        return None
    return _TYPE_LABELS.get(price_type, price_type)


@dataclass(frozen=True, slots=True)
class PremiumSnapshot:
    """Pair-level underlying + three-venue premiums for overview / detail."""

    ticker: str
    price: Decimal | None
    currency: str | None
    price_type: PriceType | None
    as_of_ms: int | None
    source: str | None
    # Default Premium column = CEX vs underlying (issue acceptance).
    premium_bps: Decimal | None
    cex_premium_bps: Decimal | None
    amm_premium_bps: Decimal | None
    rfq_premium_bps: Decimal | None
    type_label: str | None
    empty_reason: EmptyReason | None = None

    @classmethod
    def empty(cls, *, ticker: str, reason: EmptyReason) -> PremiumSnapshot:
        return cls(
            ticker=ticker,
            price=None,
            currency=None,
            price_type=None,
            as_of_ms=None,
            source=None,
            premium_bps=None,
            cex_premium_bps=None,
            amm_premium_bps=None,
            rfq_premium_bps=None,
            type_label=None,
            empty_reason=reason,
        )


def build_premium_snapshot(
    *,
    ticker: str,
    underlying: UnderlyingPriceTick | None,
    cex_mid: Decimal | None,
    amm_mid: Decimal | None,
    rfq_mid: Decimal | None,
    private: bool = False,
) -> PremiumSnapshot:
    """Assemble premiums from a latest underlying print + venue mids.

    Pure: no I/O. ``private=True`` (SPCX / uncovered) short-circuits before
    ``no_data`` so the UI can show an explicit private empty state.
    """
    if private:
        return PremiumSnapshot.empty(ticker=ticker, reason="private")
    if underlying is None:
        return PremiumSnapshot.empty(ticker=ticker, reason="no_data")

    u = underlying.price
    cex_p = premium_bps(cex_mid, u)
    amm_p = premium_bps(amm_mid, u)
    rfq_p = premium_bps(rfq_mid, u)
    pt: PriceType | None
    raw_pt = underlying.price_type
    if raw_pt in ("live", "pre", "post", "close", "stale"):
        pt = raw_pt
    else:
        pt = None
    return PremiumSnapshot(
        ticker=ticker,
        price=u,
        currency=underlying.currency,
        price_type=pt,
        as_of_ms=underlying.as_of_ms,
        source=underlying.source,
        premium_bps=cex_p,
        cex_premium_bps=cex_p,
        amm_premium_bps=amm_p,
        rfq_premium_bps=rfq_p,
        type_label=premium_type_label(pt),
        empty_reason=None,
    )
