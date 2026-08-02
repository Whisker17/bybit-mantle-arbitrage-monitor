"""Tokenized mid vs underlying equity premium (WHI-779).

Premium is measured in **bps of the underlying**:

    premium_bps = (equity_eq_tokenized_mid / underlying_price − 1) × 10⁴

Equity-equivalent mid (per share):

* **Bybit xStocks** — journal ``*_de_multiplied`` is already
  ``token_price / xstockMultiplier`` (equity space); AMM
  ``mid_usdc_per_native`` is USDC per native after ERC-4626 convert.
* **Binance bStocks** — journal ``*_de_multiplied`` is
  ``display * ui_multiplier`` (raw/on-chain space). Divide by
  ``ui_multiplier`` again so premium uses per-share display units
  matching the equity print.

Closed-session semantics: re-evaluate ``price_type`` at read time against
``as_of_ms`` + ``now_ms`` (collector may have stamped ``live`` hours ago if the
underlying poller alone stalls). UI labels via ``premium_type_label``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from monitor.metrics.config import SessionConfig
from monitor.quotes import UnderlyingPriceTick
from monitor.underlying.price_type import PriceType, classify_price_type

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
    """Mean of available RFQ buy/sell mids → single RFQ mid for premium."""
    sides = [x for x in (a, b) if x is not None and x > 0]
    if not sides:
        return None
    if len(sides) == 1:
        return sides[0]
    return (sides[0] + sides[1]) / 2


def equity_equivalent_mid(
    comparable_mid: Decimal | None,
    *,
    ui_multiplier: Decimal | None = None,
) -> Decimal | None:
    """Map journal comparable mid → per-share equity units for premium.

    ``ui_multiplier`` set (bStocks) → divide out the BEP-677 multiply that
    collection applied. ``None`` (Bybit/Fluxion) → pass through.
    """
    if comparable_mid is None or comparable_mid <= 0:
        return None
    if ui_multiplier is None:
        return comparable_mid
    if ui_multiplier <= 0:
        return None
    return comparable_mid / ui_multiplier


def premium_type_label(price_type: str | None) -> str | None:
    """Human annotation for the premium column (never silent on close/pre/post)."""
    if price_type is None:
        return None
    return _TYPE_LABELS.get(price_type, price_type)


def reclassify_underlying_for_display(
    tick: UnderlyingPriceTick,
    *,
    now_ms: int,
    session: SessionConfig,
    stale_after_open_ms: int,
    stale_after_closed_ms: int,
    stale_after_abs_ms: int,
) -> UnderlyingPriceTick:
    """Re-stamp ``price_type`` using wall-clock now (read path, WHI-779).

    Collector classification freezes at poll time; a dead Hermes feed would
    otherwise keep ``live`` forever. Stored type is the source session hint
    so explicit pre/post survive when still fresh.
    """
    hint: PriceType | None
    if tick.price_type in ("pre", "post", "live", "close"):
        hint = tick.price_type  # type: ignore[assignment]
    else:
        hint = None
    new_type = classify_price_type(
        as_of_ms=tick.as_of_ms,
        now_ms=now_ms,
        session=session,
        stale_after_open_ms=stale_after_open_ms,
        stale_after_closed_ms=stale_after_closed_ms,
        stale_after_abs_ms=stale_after_abs_ms,
        source_session_hint=hint,
    )
    if new_type == tick.price_type:
        return tick
    return replace(tick, price_type=new_type)


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
    """Assemble premiums from a latest underlying print + equity-eq venue mids.

    Pure: no I/O. Callers must pass **equity-equivalent** mids (see
    ``equity_equivalent_mid``). ``private=True`` (SPCX / uncovered)
    short-circuits before ``no_data``.
    """
    if private:
        return PremiumSnapshot.empty(ticker=ticker, reason="private")
    if underlying is None:
        return PremiumSnapshot.empty(ticker=ticker, reason="no_data")
    # WHI-794: never treat a zero/epoch print as a real equity reference.
    if underlying.price <= 0 or underlying.as_of_ms <= 0:
        return PremiumSnapshot.empty(ticker=ticker, reason="no_data")

    u = underlying.price
    cex_p = premium_bps(cex_mid, u)
    amm_p = premium_bps(amm_mid, u)
    rfq_p = premium_bps(rfq_mid, u)
    pt: PriceType | None
    raw_pt = underlying.price_type
    if raw_pt in ("live", "pre", "post", "close", "stale"):
        pt = raw_pt  # type: ignore[assignment]
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
