"""Pure multi-level book helpers for Bybit depth → VWAP (WHI-755).

Level units after de-multiply (DESIGN §2.6 / hummingbot-pnl §4.2):

    price_dm = price_raw / multiplier
    size_dm  = size_raw * multiplier   # notional = price_dm * size_dm

VWAP walks by **USD notional** (same contract as ``book_vwap_slip_bps``).
"""

from __future__ import annotations

from decimal import Decimal

from monitor.symbols.multipliers import de_multiplied_price

# bids: high→low; asks: low→high
SideMap = dict[Decimal, Decimal]


def de_multiplied_size(size: Decimal, multiplier: Decimal) -> Decimal:
    """Convert raw Bybit base size into de-multiplied base units."""
    if multiplier <= 0:
        raise ValueError(f"multiplier must be > 0, got {multiplier}")
    return size * multiplier


def apply_side_ops(
    current: SideMap,
    ops: list[tuple[Decimal, Decimal]] | None,
    *,
    is_snapshot: bool,
) -> SideMap:
    """Merge Bybit ``b``/``a`` ops into a price→size map (size 0 deletes)."""
    if is_snapshot:
        out: SideMap = {}
        if ops is None:
            return out
        for price, size in ops:
            if price <= 0:
                continue
            if size > 0:
                out[price] = size
        return out

    if ops is None or not ops:
        return current

    out = dict(current)
    for price, size in ops:
        if price <= 0:
            continue
        if size <= 0:
            out.pop(price, None)
        else:
            out[price] = size
    return out


def sorted_bid_levels(bids: SideMap) -> list[tuple[Decimal, Decimal]]:
    """Best bid first (highest price)."""
    return sorted(
        ((p, s) for p, s in bids.items() if p > 0 and s > 0),
        key=lambda x: x[0],
        reverse=True,
    )


def sorted_ask_levels(asks: SideMap) -> list[tuple[Decimal, Decimal]]:
    """Best ask first (lowest price)."""
    return sorted(
        ((p, s) for p, s in asks.items() if p > 0 and s > 0),
        key=lambda x: x[0],
    )


def best_bid(bids: SideMap) -> Decimal | None:
    levels = sorted_bid_levels(bids)
    return levels[0][0] if levels else None


def best_ask(asks: SideMap) -> Decimal | None:
    levels = sorted_ask_levels(asks)
    return levels[0][0] if levels else None


def de_multiplied_levels(
    levels: list[tuple[Decimal, Decimal]],
    multiplier: Decimal,
) -> list[tuple[Decimal, Decimal]]:
    """Apply price/m and size*m so notional is invariant."""
    if multiplier <= 0:
        raise ValueError(f"multiplier must be > 0, got {multiplier}")
    out: list[tuple[Decimal, Decimal]] = []
    for price, size in levels:
        if price <= 0 or size <= 0:
            continue
        out.append(
            (de_multiplied_price(price, multiplier), de_multiplied_size(size, multiplier))
        )
    return out


def book_vwap_for_notional(
    levels: list[tuple[Decimal, Decimal]],
    size_usd: Decimal,
) -> Decimal | None:
    """Average fill price walking ``levels`` for ``size_usd`` notional.

    Returns None if the book cannot fully fill (no silent partials).
    """
    if size_usd <= 0:
        raise ValueError("size_usd must be positive")
    spent = Decimal(0)
    qty = Decimal(0)
    for px, sz in levels:
        if px <= 0 or sz <= 0:
            continue
        avail = px * sz
        take = min(avail, size_usd - spent)
        if take <= 0:
            break
        qty += take / px
        spent += take
        if spent >= size_usd:
            break
    if qty <= 0 or spent + Decimal("1e-9") < size_usd:
        return None
    return spent / qty


def book_vwap_for_base(
    levels: list[tuple[Decimal, Decimal]],
    base_qty: Decimal,
) -> Decimal | None:
    """Average fill price walking ``levels`` for ``base_qty`` base units.

    PnL v2 normative walk (hummingbot-pnl §4.2 / Hummingbot ``get_vwap_for_volume``).
    Levels are ``(price, size)`` in de-multiplied units. Returns None if the book
    cannot fully fill (no silent partials).
    """
    if base_qty <= 0:
        raise ValueError("base_qty must be positive")
    remaining = base_qty
    notional = Decimal(0)
    for px, sz in levels:
        if px <= 0 or sz <= 0:
            continue
        take = min(sz, remaining)
        notional += take * px
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0:
        return None
    return notional / base_qty


def book_notional_depth(levels: list[tuple[Decimal, Decimal]]) -> Decimal:
    """Total USD notional available on ``levels`` (de-multiplied)."""
    total = Decimal(0)
    for px, sz in levels:
        if px > 0 and sz > 0:
            total += px * sz
    return total


def vwap_curve(
    levels_dm: list[tuple[Decimal, Decimal]],
    buckets_usd: list[Decimal],
) -> list[Decimal | None]:
    """Precompute VWAP at each USD bucket (de-multiplied levels)."""
    return [book_vwap_for_notional(levels_dm, q) for q in buckets_usd]
