"""AMM mid quotability — single seam for empty / untradable pools (WHI-795)
and extreme CEX⇄AMM basis (WHI-822).

Uniswap V3 ``slot0.sqrtPriceX96`` survives after all in-range liquidity is
withdrawn. Residual mid is **not** a tradable quote: any size is unfillable,
and comparing it to CEX produces phantom spreads (e.g. +1016 bps / −10000 bps).

Separately, a liquid pool can print a mid that is wildly detached from the
CEX book (SPYB +1000s of bps with L>0 and six-figure TVL). That mid is still
a real slot0 reading — inventory / decimals / multiplier were verified on
chain (WHI-822) — but it must not be promoted to a **tradable** paper-PnL
or Top-N claim without independent evidence. Prefer false negative over a
fake fillable opportunity.

All consumers that claim a **tradable** AMM mid for price, spread, net edge,
or bucket PnL must go through :func:`amm_quote_for_cex` (or the equivalent
``quotable_amm_mid`` + :func:`annotate_pricing_anomaly` pair) when a CEX mid
is available. Premium vs underlying may still use :func:`quotable_amm_mid`
alone for empty-pool suppression; the overview row carries
``amm_quote_reason`` from the spread path for badges. Attribution pre-trade
mids (e.g. ``_pool_mid_pre``) may still read raw ``mid_usdc_per_native`` —
a swap implies liquidity existed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from monitor.quotes import FluxionPoolStateTick

# 1e4 — same scale as monitor.metrics.bybit_slip.BPS; kept local so this seam
# does not import venue-named modules.
_BPS = Decimal(10_000)

# Wire reason codes (stable API / journal-adjacent). UI maps to human labels.
# ``pricing_anomaly`` is set *with* a non-null mid (contrast empty_pool which
# nulls the mid). Callers that claim tradable edge/PnL must treat any non-null
# reason as non-tradable even when mid is present.
AmmQuoteReason = Literal["empty_pool", "invalid_mid", "pricing_anomaly"]


def quotable_amm_mid(
    tick: FluxionPoolStateTick | None,
) -> tuple[Decimal | None, AmmQuoteReason | None]:
    """Return ``(mid, reason)`` for AMM quote use.

    * ``tick is None`` → ``(None, None)`` — caller distinguishes no-tick /
      no-pool / stale via its own inventory and freshness paths.
    * ``liquidity <= 0`` → ``(None, "empty_pool")`` — residual slot0 mid discarded.
    * ``mid_usdc_per_native <= 0`` → ``(None, "invalid_mid")``.
    * else → ``(mid, None)``.

    Does **not** apply the |vs CEX| magnitude guard — that needs a CEX mid.
    Prefer :func:`amm_quote_for_cex` at tradable seams.
    """
    if tick is None:
        return None, None
    if tick.liquidity <= 0:
        return None, "empty_pool"
    mid = tick.mid_usdc_per_native
    if mid <= 0:
        return None, "invalid_mid"
    return mid, None


def annotate_pricing_anomaly(
    mid: Decimal | None,
    reason: AmmQuoteReason | None,
    *,
    cex_mid: Decimal,
    max_abs_spread_bps: Decimal | None,
) -> tuple[Decimal | None, AmmQuoteReason | None]:
    """Layer |AMM − CEX| / CEX magnitude guard on a quotable mid (WHI-822).

    * Existing non-null ``reason`` (empty_pool / invalid_mid) is preserved.
    * ``max_abs_spread_bps is None`` disables the guard (explicit config intent).
    * When ``cex_mid <= 0`` the comparison is undefined; prefer false negative
      and return ``pricing_anomaly`` if a mid would otherwise be tradable.
    * When ``abs((mid - cex_mid) / cex_mid * 1e4) > max_abs_spread_bps``,
      returns ``(mid, "pricing_anomaly")`` — **mid is kept** so the panel can
      show the wild basis; tradable consumers must still refuse the claim.
    """
    if mid is None or reason is not None:
        return mid, reason
    if max_abs_spread_bps is None:
        return mid, None
    if cex_mid <= 0:
        # Cannot verify basis — fail closed (prefer miss over fake claim).
        return mid, "pricing_anomaly"
    abs_bps = abs((mid - cex_mid) / cex_mid * _BPS)
    if abs_bps > max_abs_spread_bps:
        return mid, "pricing_anomaly"
    return mid, None


def amm_quote_for_cex(
    tick: FluxionPoolStateTick | None,
    *,
    cex_mid: Decimal,
    max_abs_spread_bps: Decimal | None,
) -> tuple[Decimal | None, AmmQuoteReason | None]:
    """Quotability + |vs CEX| guard in one call (preferred tradable seam)."""
    mid, reason = quotable_amm_mid(tick)
    return annotate_pricing_anomaly(
        mid,
        reason,
        cex_mid=cex_mid,
        max_abs_spread_bps=max_abs_spread_bps,
    )


def is_tradable_amm_quote(reason: AmmQuoteReason | None) -> bool:
    """True only when AMM mid may feed paper edge / PnL optimal / Top-N seats."""
    return reason is None


__all__ = [
    "AmmQuoteReason",
    "amm_quote_for_cex",
    "annotate_pricing_anomaly",
    "is_tradable_amm_quote",
    "quotable_amm_mid",
]
