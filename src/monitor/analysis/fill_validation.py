"""On-chain fill validation for paper dislocation windows (WHI-908 / M8).

Pure classification helpers. Callers supply already-decoded journal swaps and
window geometry; this module never touches SQLite or RPC.

Maps each paper opportunity window to one of:

* ``taken`` — at least one on-chain swap in the profitable AMM direction
  during the window (validates the dislocation was real and firm enough
  for someone to trade).
* ``untaken_with_liquidity`` — no matching swap, but in-range depth would
  have supported the study notional.
* ``untaken_too_thin`` — no matching swap and depth could not support the
  notional (the paper "opportunity" was never capturable at size).

Direction convention matches ``monitor.fluxion.events`` decode:

* ``buy_native``  — trader buys the stock token on Fluxion (pool loses base)
* ``sell_native`` — trader sells the stock token on Fluxion (pool gains base)

Paper directions (``monitor.metrics.pnl_v2``):

* ``buy_fluxion_sell_bybit``  → profitable AMM leg is ``buy_native``
* ``buy_bybit_sell_fluxion``  → profitable AMM leg is ``sell_native``
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from monitor.metrics.amm_pool import AmmPoolState
from monitor.metrics.amm_slip import Q96, fluxion_amm_slip_bps

WindowClass = Literal["taken", "untaken_with_liquidity", "untaken_too_thin"]

_BPS = Decimal(10_000)


@dataclass(frozen=True, slots=True)
class DecodedSwapView:
    """Minimal swap fields needed for window matching (journal-decoded)."""

    pair_id: str
    recv_ts_ms: int
    block_number: int
    block_ts: int
    tx_hash: str
    log_index: int
    direction: str
    # Human-unit legs (token0 / token1 as stored on the tick).
    amount_token0: Decimal
    amount_token1: Decimal
    price_usdc_per_wrapper: Decimal | None
    # USD notional proxy: prefer |quote leg|, else |base| × mid.
    notional_usd: Decimal
    # Fill price (quote per base) from signed amounts, not post-swap mid.
    effective_price: Decimal | None


@dataclass(frozen=True, slots=True)
class WindowMatch:
    """Classification result for one paper opportunity window."""

    classification: WindowClass
    profitable_swap_direction: str
    n_swaps_total: int
    n_swaps_matching: int
    matching_swaps: tuple[DecodedSwapView, ...]
    depth_adequate: bool
    virtual_quote_usd: Decimal | None
    size_usd: Decimal


def profitable_swap_direction(paper_direction: str) -> str:
    """Map a paper arb direction to the on-chain Fluxion swap direction."""
    if paper_direction == "buy_fluxion_sell_bybit":
        return "buy_native"
    if paper_direction == "buy_bybit_sell_fluxion":
        return "sell_native"
    raise ValueError(
        f"unknown paper direction {paper_direction!r}; "
        "expected buy_fluxion_sell_bybit|buy_bybit_sell_fluxion"
    )


def swaps_in_window(
    swaps: Sequence[DecodedSwapView],
    *,
    start_ms: int,
    end_ms: int,
) -> list[DecodedSwapView]:
    """Swaps whose ``recv_ts_ms`` falls inside ``[start_ms, end_ms]`` inclusive."""
    if end_ms < start_ms:
        raise ValueError("end_ms must be >= start_ms")
    return [s for s in swaps if start_ms <= s.recv_ts_ms <= end_ms]


def matching_profitable_swaps(
    swaps: Sequence[DecodedSwapView],
    *,
    paper_direction: str,
) -> list[DecodedSwapView]:
    """Filter swaps whose direction matches the paper opportunity's AMM leg."""
    want = profitable_swap_direction(paper_direction)
    return [s for s in swaps if s.direction == want]


def depth_supports_size(
    pool: AmmPoolState,
    *,
    size_usd: Decimal,
    paper_direction: str,
) -> bool:
    """True when UniV3 single-range math fills ``size_usd`` on the AMM leg.

    Uses the same pure slip path as PnL v2 (no tick-crossing). A ``False``
    result means the paper notional is not capturable in the current range —
    class (c) territory when also untaken.
    """
    if size_usd <= 0:
        return False
    fluxion_leg: Literal["buy", "sell"]
    if paper_direction == "buy_fluxion_sell_bybit":
        fluxion_leg = "buy"
    elif paper_direction == "buy_bybit_sell_fluxion":
        fluxion_leg = "sell"
    else:
        raise ValueError(f"unknown paper direction {paper_direction!r}")
    result = fluxion_amm_slip_bps(pool, size_usd=size_usd, direction=fluxion_leg)
    return result.fillable


def virtual_quote_side_usd(pool: AmmPoolState) -> Decimal:
    """Virtual USDC-side reserves implied by (L, sqrtP) in the current range.

    Not tradeable depth (tick bounds matter), but a comparable scale to the
    ~$41–57k figures cited in the M8 go-case narrative. Quote is treated as
    $1 stablecoin.
    """
    if pool.liquidity <= 0 or pool.sqrt_price_x96 <= 0:
        return Decimal(0)
    L = Decimal(pool.liquidity)
    sp = Decimal(pool.sqrt_price_x96)
    # UniV3 virtual reserves: x=L·2^96/√P (token0), y=L·√P/2^96 (token1).
    if pool.token0_is_quote:
        raw = L * Q96 / sp
        return raw / (Decimal(10) ** pool.token0_decimals)
    raw = L * sp / Q96
    return raw / (Decimal(10) ** pool.token1_decimals)


def classify_window(
    *,
    paper_direction: str,
    size_usd: Decimal,
    swaps_in_range: Sequence[DecodedSwapView],
    pool: AmmPoolState | None,
) -> WindowMatch:
    """Classify one paper window given its in-window swaps and pool snapshot.

    ``pool`` is the as-of pool state at window open (caller responsibility).
    When ``pool is None`` (no geometry), depth is treated as inadequate.
    """
    if size_usd <= 0:
        raise ValueError("size_usd must be positive")
    want = profitable_swap_direction(paper_direction)
    matching = matching_profitable_swaps(
        swaps_in_range, paper_direction=paper_direction
    )
    if pool is None:
        depth_ok = False
        vq: Decimal | None = None
    else:
        depth_ok = depth_supports_size(
            pool, size_usd=size_usd, paper_direction=paper_direction
        )
        vq = virtual_quote_side_usd(pool)

    if matching:
        klass: WindowClass = "taken"
    elif depth_ok:
        klass = "untaken_with_liquidity"
    else:
        klass = "untaken_too_thin"

    return WindowMatch(
        classification=klass,
        profitable_swap_direction=want,
        n_swaps_total=len(swaps_in_range),
        n_swaps_matching=len(matching),
        matching_swaps=tuple(matching),
        depth_adequate=depth_ok,
        virtual_quote_usd=vq,
        size_usd=size_usd,
    )


def pool_age_stats(ages_ms: Sequence[int]) -> dict[str, int | float]:
    """Summarise as-of join ages (book_ts − pool_ts) for samples in a window."""
    if not ages_ms:
        return {
            "n": 0,
            "min_ms": 0,
            "median_ms": 0,
            "p90_ms": 0,
            "max_ms": 0,
            "mean_ms": 0.0,
        }
    ordered = sorted(int(a) for a in ages_ms)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        median = ordered[mid]
    else:
        median = (ordered[mid - 1] + ordered[mid]) // 2
    p90_idx = min(n - 1, max(0, (n * 9) // 10))
    return {
        "n": n,
        "min_ms": ordered[0],
        "median_ms": median,
        "p90_ms": ordered[p90_idx],
        "max_ms": ordered[-1],
        "mean_ms": sum(ordered) / n,
    }


def abs_basis_bps(*, amm_mid: Decimal, cex_mid: Decimal) -> Decimal | None:
    """|AMM − CEX| / CEX in bps; None when either mid is non-positive."""
    if amm_mid <= 0 or cex_mid <= 0:
        return None
    return abs(amm_mid - cex_mid) / cex_mid * _BPS


def swap_notional_usd(
    *,
    amount_token0: Decimal,
    amount_token1: Decimal,
    token0_is_quote: bool,
    price_usdc_per_wrapper: Decimal | None,
) -> Decimal:
    """USD notional proxy for a decoded swap.

    Prefer the absolute quote-leg size when token order is known; fall back to
    base × mid when only the base leg is informative.
    """
    a0 = abs(amount_token0)
    a1 = abs(amount_token1)
    if token0_is_quote:
        quote_leg = a0
        base_leg = a1
    else:
        quote_leg = a1
        base_leg = a0
    if quote_leg > 0:
        return quote_leg
    if price_usdc_per_wrapper is not None and price_usdc_per_wrapper > 0 and base_leg > 0:
        return base_leg * price_usdc_per_wrapper
    return Decimal(0)


def effective_fill_price(
    *,
    amount_token0: Decimal,
    amount_token1: Decimal,
    token0_is_quote: bool,
) -> Decimal | None:
    """Average fill price (quote per base) from the two human-unit legs.

    Distinct from post-swap pool mid (``price_usdc_per_wrapper`` on the journal
    tick): this is |quote transferred| / |base transferred|.
    """
    a0 = abs(amount_token0)
    a1 = abs(amount_token1)
    if token0_is_quote:
        quote_leg, base_leg = a0, a1
    else:
        quote_leg, base_leg = a1, a0
    if base_leg <= 0 or quote_leg <= 0:
        return None
    return quote_leg / base_leg


__all__ = [
    "DecodedSwapView",
    "WindowClass",
    "WindowMatch",
    "abs_basis_bps",
    "classify_window",
    "depth_supports_size",
    "effective_fill_price",
    "matching_profitable_swaps",
    "pool_age_stats",
    "profitable_swap_direction",
    "swap_notional_usd",
    "swaps_in_window",
    "virtual_quote_side_usd",
]
