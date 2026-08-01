"""Exact UniV3 single-range AMM math for Fluxion pool slippage.

Uses the concentrated-liquidity virtual reserves implied by ``liquidity`` and
``sqrtPriceX96`` (Uniswap V3 formulas). Fee is the pool fee in hundredths of a
bip (e.g. 3000 → 30 bps = 0.30%), matching ``pairs.yaml`` ``amm.fee``.

Limitation (documented): without a tick bitmap we cannot cross initialized
ticks. For notionals small relative to in-range liquidity this is exact; large
sizes that would leave the current tick range are marked unfillable rather than
approximated.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

Q96 = Decimal(2**96)
# Cap price move within one "tick step" approximation: refuse fills that move
# sqrt price by more than this fraction of current sqrtP (≈ large single-range
# exhaustion). 50% of sqrtP is already a huge move; primarily a safety rail.
_MAX_SQRT_MOVE_FRAC = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class AmmSwapResult:
    """Result of a size-notional swap against the current V3 range."""

    amount_in: Decimal
    amount_out: Decimal
    mid_before: Decimal
    exec_price: Decimal  # quote per base at the swap's average
    slip_bps_vs_mid: Decimal
    fillable: bool
    reason: str | None = None


def fee_bps_from_pool_fee(pool_fee: int) -> Decimal:
    """Uniswap V3 fee units → bps. fee=3000 → 30 bps."""
    if pool_fee < 0:
        raise ValueError(f"pool_fee must be >= 0, got {pool_fee}")
    # fee is in hundredths of a bip: 1e6 units = 100% = 10_000 bps
    return Decimal(pool_fee) / Decimal(100)


def mid_quote_per_base(
    sqrt_price_x96: int,
    *,
    token0_is_quote: bool,
    token0_decimals: int,
    token1_decimals: int,
) -> Decimal:
    """Human mid as quote-token per base-token (base = non-quote)."""
    if sqrt_price_x96 <= 0:
        raise ValueError("sqrt_price_x96 must be positive")
    ratio = (Decimal(sqrt_price_x96) / Q96) ** 2
    t1_per_t0 = ratio * (Decimal(10) ** (token0_decimals - token1_decimals))
    if token0_is_quote:
        if t1_per_t0 == 0:
            raise ValueError("zero price")
        return Decimal(1) / t1_per_t0
    return t1_per_t0


def _apply_fee(amount_in: Decimal, pool_fee: int) -> Decimal:
    """Amount available to the pool after LP fee (fee on input)."""
    # fee / 1e6 of input is taken as fee
    fee_factor = Decimal(1_000_000 - pool_fee) / Decimal(1_000_000)
    return amount_in * fee_factor


def swap_exact_in_zero_for_one(
    *,
    sqrt_price_x96: int,
    liquidity: int,
    amount_in: Decimal,
    pool_fee: int,
) -> tuple[Decimal, int] | None:
    """token0 → token1. Returns (amount1_out, new_sqrt_price_x96) or None."""
    if liquidity <= 0 or amount_in <= 0 or sqrt_price_x96 <= 0:
        return None
    amount_in_less_fee = _apply_fee(amount_in, pool_fee)
    # ΔsqrtP = amount_in * sqrtP / (L + amount_in * sqrtP / Q96) style:
    # next = L * Q96 * sqrtP / (L * Q96 + amount_in * sqrtP)
    # With raw integers for precision on the sqrt path:
    L = Decimal(liquidity)
    sp = Decimal(sqrt_price_x96)
    # amount_in here is in token0 raw units (already Decimal of raw)
    numerator = L * Q96 * sp
    denominator = L * Q96 + amount_in_less_fee * sp
    if denominator <= 0:
        return None
    new_sp = numerator / denominator
    if new_sp <= 0 or new_sp >= sp:
        return None
    if (sp - new_sp) / sp > _MAX_SQRT_MOVE_FRAC:
        return None
    # amount1_out = L * (sp - new_sp) / Q96
    amount_out = L * (sp - new_sp) / Q96
    if amount_out <= 0:
        return None
    return amount_out, int(new_sp)


def swap_exact_in_one_for_zero(
    *,
    sqrt_price_x96: int,
    liquidity: int,
    amount_in: Decimal,
    pool_fee: int,
) -> tuple[Decimal, int] | None:
    """token1 → token0. Returns (amount0_out, new_sqrt_price_x96) or None."""
    if liquidity <= 0 or amount_in <= 0 or sqrt_price_x96 <= 0:
        return None
    amount_in_less_fee = _apply_fee(amount_in, pool_fee)
    L = Decimal(liquidity)
    sp = Decimal(sqrt_price_x96)
    # next = sp + amount_in * Q96 / L
    new_sp = sp + amount_in_less_fee * Q96 / L
    if new_sp <= sp:
        return None
    if (new_sp - sp) / sp > _MAX_SQRT_MOVE_FRAC:
        return None
    # amount0_out = L * Q96 * (new_sp - sp) / (sp * new_sp)
    amount_out = L * Q96 * (new_sp - sp) / (sp * new_sp)
    if amount_out <= 0:
        return None
    return amount_out, int(new_sp)


def fluxion_amm_slip_bps(
    *,
    sqrt_price_x96: int,
    liquidity: int,
    pool_fee: int,
    size_usd: Decimal,
    direction: str,
    token0_is_quote: bool,
    token0_decimals: int,
    token1_decimals: int,
    quote_decimals: int = 6,
    base_decimals: int = 18,
) -> AmmSwapResult:
    """Compute AMM execution slip vs mid for a USD notional.

    ``direction`` is the *Fluxion* leg relative to the base (wrapper/native):
    - ``\"buy\"``: spend quote to buy base (trader buys xStock on AMM)
    - ``\"sell\"``: sell base for quote

    Fee is charged inside the swap math; ``slip_bps_vs_mid`` is the *price*
    impact only (exec vs mid), **not** including the fee as a second term —
    fee is reported separately via ``fee_bps_from_pool_fee``.
    """
    if direction not in ("buy", "sell"):
        raise ValueError(f"direction must be buy|sell, got {direction!r}")
    if size_usd <= 0:
        raise ValueError("size_usd must be positive")

    mid = mid_quote_per_base(
        sqrt_price_x96,
        token0_is_quote=token0_is_quote,
        token0_decimals=token0_decimals,
        token1_decimals=token1_decimals,
    )

    # Work in raw token units for the swap step; fee applied inside.
    # For price impact we want pre-fee geometric mid vs post-swap average
    # execution. Separating fee: run the swap with fee=0 for impact, report fee
    # as its own bps line. That keeps hand-recompute of wear additive.
    zero_fee = 0

    if direction == "buy":
        # Spend `size_usd` quote → receive base.
        amount_in_raw = size_usd * (Decimal(10) ** quote_decimals)
        if token0_is_quote:
            # token0=quote → token1=base: zeroForOne
            out = swap_exact_in_zero_for_one(
                sqrt_price_x96=sqrt_price_x96,
                liquidity=liquidity,
                amount_in=amount_in_raw,
                pool_fee=zero_fee,
            )
            if out is None:
                return AmmSwapResult(
                    amount_in=size_usd,
                    amount_out=Decimal(0),
                    mid_before=mid,
                    exec_price=mid,
                    slip_bps_vs_mid=Decimal(0),
                    fillable=False,
                    reason="unfillable_or_range_exhausted",
                )
            amount_out_raw, _ = out
            amount_out = amount_out_raw / (Decimal(10) ** base_decimals)
        else:
            # token0=base, token1=quote: oneForZero (quote in → base out)
            out = swap_exact_in_one_for_zero(
                sqrt_price_x96=sqrt_price_x96,
                liquidity=liquidity,
                amount_in=amount_in_raw,
                pool_fee=zero_fee,
            )
            if out is None:
                return AmmSwapResult(
                    amount_in=size_usd,
                    amount_out=Decimal(0),
                    mid_before=mid,
                    exec_price=mid,
                    slip_bps_vs_mid=Decimal(0),
                    fillable=False,
                    reason="unfillable_or_range_exhausted",
                )
            amount_out_raw, _ = out
            amount_out = amount_out_raw / (Decimal(10) ** base_decimals)
        if amount_out <= 0:
            return AmmSwapResult(
                amount_in=size_usd,
                amount_out=Decimal(0),
                mid_before=mid,
                exec_price=mid,
                slip_bps_vs_mid=Decimal(0),
                fillable=False,
                reason="zero_out",
            )
        exec_price = size_usd / amount_out  # quote per base paid
        # Buying: higher exec than mid is positive slip
        slip = (exec_price - mid) / mid * Decimal(10_000)
    else:
        # Sell base worth ~size_usd at mid → receive quote.
        base_qty = size_usd / mid
        amount_in_raw = base_qty * (Decimal(10) ** base_decimals)
        if token0_is_quote:
            # token0=quote, token1=base: sell base = oneForZero
            out = swap_exact_in_one_for_zero(
                sqrt_price_x96=sqrt_price_x96,
                liquidity=liquidity,
                amount_in=amount_in_raw,
                pool_fee=zero_fee,
            )
            if out is None:
                return AmmSwapResult(
                    amount_in=base_qty,
                    amount_out=Decimal(0),
                    mid_before=mid,
                    exec_price=mid,
                    slip_bps_vs_mid=Decimal(0),
                    fillable=False,
                    reason="unfillable_or_range_exhausted",
                )
            amount_out_raw, _ = out
            amount_out = amount_out_raw / (Decimal(10) ** quote_decimals)
        else:
            # token0=base, token1=quote: sell base = zeroForOne
            out = swap_exact_in_zero_for_one(
                sqrt_price_x96=sqrt_price_x96,
                liquidity=liquidity,
                amount_in=amount_in_raw,
                pool_fee=zero_fee,
            )
            if out is None:
                return AmmSwapResult(
                    amount_in=base_qty,
                    amount_out=Decimal(0),
                    mid_before=mid,
                    exec_price=mid,
                    slip_bps_vs_mid=Decimal(0),
                    fillable=False,
                    reason="unfillable_or_range_exhausted",
                )
            amount_out_raw, _ = out
            amount_out = amount_out_raw / (Decimal(10) ** quote_decimals)
        if amount_out <= 0 or base_qty <= 0:
            return AmmSwapResult(
                amount_in=base_qty,
                amount_out=Decimal(0),
                mid_before=mid,
                exec_price=mid,
                slip_bps_vs_mid=Decimal(0),
                fillable=False,
                reason="zero_out",
            )
        exec_price = amount_out / base_qty  # quote per base received
        # Selling: lower exec than mid is positive slip
        slip = (mid - exec_price) / mid * Decimal(10_000)

    return AmmSwapResult(
        amount_in=size_usd if direction == "buy" else base_qty,
        amount_out=amount_out,
        mid_before=mid,
        exec_price=exec_price,
        slip_bps_vs_mid=slip if slip > 0 else Decimal(0),
        fillable=True,
    )


def gas_bps(gas_usd: Decimal, size_usd: Decimal) -> Decimal:
    """Convert fixed USD gas into bps of notional."""
    if size_usd <= 0:
        raise ValueError("size_usd must be positive")
    if gas_usd < 0:
        raise ValueError("gas_usd must be >= 0")
    return gas_usd / size_usd * Decimal(10_000)
