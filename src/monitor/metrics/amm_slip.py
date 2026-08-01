"""Exact UniV3 single-range AMM math for Fluxion pool slippage.

Uses the concentrated-liquidity virtual reserves implied by ``liquidity`` and
``sqrtPriceX96`` (Uniswap V3 formulas). Fee is the pool fee in hundredths of a
bip (e.g. 3000 → 30 bps = 0.30%), matching ``pairs.yaml`` ``amm.fee``.

Limitation (documented): without a tick bitmap we cannot cross initialized
ticks. For notionals small relative to in-range liquidity this is exact; large
sizes that would leave the current tick range are marked unfillable rather than
approximated with a silent constant-liquidity assumption. DESIGN §4.2 prefers
contract Quoter when RPC is available; this pure path is for offline recomputes
and the TUI hot path without an extra eth_call.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from monitor.metrics.amm_pool import AmmPoolState
from monitor.metrics.bybit_slip import BPS

Q96 = Decimal(2**96)
FluxionLeg = Literal["buy", "sell"]
# Cap price move within one range step: refuse fills that move sqrt price by
# more than this fraction of current sqrtP (safety rail vs silent understate).
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
    return Decimal(pool_fee) / Decimal(100)


def _unfillable(mid: Decimal, amount_in: Decimal, reason: str) -> AmmSwapResult:
    return AmmSwapResult(
        amount_in=amount_in,
        amount_out=Decimal(0),
        mid_before=mid,
        exec_price=mid,
        slip_bps_vs_mid=Decimal(0),
        fillable=False,
        reason=reason,
    )


def _apply_fee(amount_in: Decimal, pool_fee: int) -> Decimal:
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
    L = Decimal(liquidity)
    sp = Decimal(sqrt_price_x96)
    numerator = L * Q96 * sp
    denominator = L * Q96 + amount_in_less_fee * sp
    if denominator <= 0:
        return None
    new_sp = numerator / denominator
    if new_sp <= 0 or new_sp >= sp:
        return None
    if (sp - new_sp) / sp > _MAX_SQRT_MOVE_FRAC:
        return None
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
    new_sp = sp + amount_in_less_fee * Q96 / L
    if new_sp <= sp:
        return None
    if (new_sp - sp) / sp > _MAX_SQRT_MOVE_FRAC:
        return None
    amount_out = L * Q96 * (new_sp - sp) / (sp * new_sp)
    if amount_out <= 0:
        return None
    return amount_out, int(new_sp)


def fluxion_amm_slip_bps(
    pool: AmmPoolState,
    *,
    size_usd: Decimal,
    direction: FluxionLeg,
) -> AmmSwapResult:
    """Compute AMM execution slip vs mid for a USD notional.

    ``direction`` is the *Fluxion* leg relative to the base (wrapper/native):
    - ``\"buy\"``: spend quote to buy base
    - ``\"sell\"``: sell base for quote

    Fee is charged as a separate wear line via ``fee_bps_from_pool_fee``;
    ``slip_bps_vs_mid`` is price impact only (swap math run with fee=0).
    """
    if direction not in ("buy", "sell"):
        raise ValueError(f"direction must be buy|sell, got {direction!r}")
    if size_usd <= 0:
        raise ValueError("size_usd must be positive")

    mid = pool.mid_quote_per_base()
    quote_decimals = pool.quote_decimals
    base_decimals = pool.base_decimals
    # Impact only — fee reported separately so wear stays additive.
    zero_fee = 0

    if direction == "buy":
        amount_in_raw = size_usd * (Decimal(10) ** quote_decimals)
        if pool.token0_is_quote:
            out = swap_exact_in_zero_for_one(
                sqrt_price_x96=pool.sqrt_price_x96,
                liquidity=pool.liquidity,
                amount_in=amount_in_raw,
                pool_fee=zero_fee,
            )
        else:
            out = swap_exact_in_one_for_zero(
                sqrt_price_x96=pool.sqrt_price_x96,
                liquidity=pool.liquidity,
                amount_in=amount_in_raw,
                pool_fee=zero_fee,
            )
        if out is None:
            return _unfillable(mid, size_usd, "unfillable_or_range_exhausted")
        amount_out_raw, _ = out
        amount_out = amount_out_raw / (Decimal(10) ** base_decimals)
        if amount_out <= 0:
            return _unfillable(mid, size_usd, "zero_out")
        exec_price = size_usd / amount_out
        slip = (exec_price - mid) / mid * BPS
        return AmmSwapResult(
            amount_in=size_usd,
            amount_out=amount_out,
            mid_before=mid,
            exec_price=exec_price,
            slip_bps_vs_mid=slip if slip > 0 else Decimal(0),
            fillable=True,
        )

    base_qty = size_usd / mid
    amount_in_raw = base_qty * (Decimal(10) ** base_decimals)
    if pool.token0_is_quote:
        out = swap_exact_in_one_for_zero(
            sqrt_price_x96=pool.sqrt_price_x96,
            liquidity=pool.liquidity,
            amount_in=amount_in_raw,
            pool_fee=zero_fee,
        )
    else:
        out = swap_exact_in_zero_for_one(
            sqrt_price_x96=pool.sqrt_price_x96,
            liquidity=pool.liquidity,
            amount_in=amount_in_raw,
            pool_fee=zero_fee,
        )
    if out is None:
        return _unfillable(mid, base_qty, "unfillable_or_range_exhausted")
    amount_out_raw, _ = out
    amount_out = amount_out_raw / (Decimal(10) ** quote_decimals)
    if amount_out <= 0 or base_qty <= 0:
        return _unfillable(mid, base_qty, "zero_out")
    exec_price = amount_out / base_qty
    slip = (mid - exec_price) / mid * BPS
    return AmmSwapResult(
        amount_in=base_qty,
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
    return gas_usd / size_usd * BPS


def _swap_quote_in_for_base_out(
    pool: AmmPoolState,
    quote_in: Decimal,
    *,
    pool_fee: int,
) -> Decimal | None:
    """Exact-in quote → base (human units). None if unfillable."""
    if quote_in <= 0:
        return None
    amount_in_raw = quote_in * (Decimal(10) ** pool.quote_decimals)
    if pool.token0_is_quote:
        out = swap_exact_in_zero_for_one(
            sqrt_price_x96=pool.sqrt_price_x96,
            liquidity=pool.liquidity,
            amount_in=amount_in_raw,
            pool_fee=pool_fee,
        )
    else:
        out = swap_exact_in_one_for_zero(
            sqrt_price_x96=pool.sqrt_price_x96,
            liquidity=pool.liquidity,
            amount_in=amount_in_raw,
            pool_fee=pool_fee,
        )
    if out is None:
        return None
    amount_out_raw, _ = out
    amount_out = amount_out_raw / (Decimal(10) ** pool.base_decimals)
    return amount_out if amount_out > 0 else None


def _swap_base_in_for_quote_out(
    pool: AmmPoolState,
    base_in: Decimal,
    *,
    pool_fee: int,
) -> Decimal | None:
    """Exact-in base → quote (human units). None if unfillable."""
    if base_in <= 0:
        return None
    amount_in_raw = base_in * (Decimal(10) ** pool.base_decimals)
    if pool.token0_is_quote:
        out = swap_exact_in_one_for_zero(
            sqrt_price_x96=pool.sqrt_price_x96,
            liquidity=pool.liquidity,
            amount_in=amount_in_raw,
            pool_fee=pool_fee,
        )
    else:
        out = swap_exact_in_zero_for_one(
            sqrt_price_x96=pool.sqrt_price_x96,
            liquidity=pool.liquidity,
            amount_in=amount_in_raw,
            pool_fee=pool_fee,
        )
    if out is None:
        return None
    amount_out_raw, _ = out
    amount_out = amount_out_raw / (Decimal(10) ** pool.quote_decimals)
    return amount_out if amount_out > 0 else None


def amm_quote_out_for_base_in(
    pool: AmmPoolState,
    base_in: Decimal,
    *,
    apply_pool_fee: bool = True,
) -> Decimal | None:
    """Sell ``base_in`` on the AMM; return fee-inclusive USDC out (or None)."""
    fee = pool.pool_fee if apply_pool_fee else 0
    return _swap_base_in_for_quote_out(pool, base_in, pool_fee=fee)


def amm_base_out_for_quote_in(
    pool: AmmPoolState,
    quote_in: Decimal,
    *,
    apply_pool_fee: bool = True,
) -> Decimal | None:
    """Buy base with ``quote_in`` USDC; return base out (or None)."""
    fee = pool.pool_fee if apply_pool_fee else 0
    return _swap_quote_in_for_base_out(pool, quote_in, pool_fee=fee)


def amm_quote_in_for_base_out(
    pool: AmmPoolState,
    base_out: Decimal,
    *,
    q_tol_rel: Decimal = Decimal("1e-6"),
    max_iters: int = 64,
    apply_pool_fee: bool = True,
) -> Decimal | None:
    """Binary-search exact-in quote needed so base out equals ``base_out``.

    Normative PnL v2 buy-Fluxion path (hummingbot-pnl §4.3.1). Returns None on
    range exhaustion or solver failure.
    """
    if base_out <= 0:
        raise ValueError("base_out must be positive")
    if q_tol_rel <= 0:
        raise ValueError("q_tol_rel must be positive")
    if max_iters < 1:
        raise ValueError("max_iters must be >= 1")

    mid = pool.mid_quote_per_base()
    if mid <= 0:
        return None

    fee = pool.pool_fee if apply_pool_fee else 0
    # Seed bounds: fee-adjusted mid cost, expand hi until base_out is covered.
    fee_factor = Decimal(1_000_000 - fee) / Decimal(1_000_000) if fee else Decimal(1)
    if fee_factor <= 0:
        return None
    lo = base_out * mid * Decimal("0.5")
    hi = base_out * mid / fee_factor * Decimal("1.5")
    if lo <= 0:
        lo = Decimal("1e-12")

    def _base_at(quote_in: Decimal) -> Decimal | None:
        return _swap_quote_in_for_base_out(pool, quote_in, pool_fee=fee)

    # Expand hi until fillable and base_out_at(hi) >= target (or give up).
    expanded = 0
    while expanded < 48:
        got = _base_at(hi)
        if got is not None and got >= base_out:
            break
        if got is None and hi > base_out * mid * Decimal(1000):
            return None
        hi *= 2
        expanded += 1
    else:
        return None

    # Ensure lo is below target (may already overshoot on tiny pools).
    got_lo = _base_at(lo)
    if got_lo is not None and got_lo >= base_out:
        # Even lo fills — shrink lo toward 0.
        lo = lo / 2 if lo > 0 else Decimal("1e-18")
        for _ in range(32):
            got_lo = _base_at(lo)
            if got_lo is None or got_lo < base_out:
                break
            lo = lo / 2

    best_quote: Decimal | None = None
    for _ in range(max_iters):
        mid_q = (lo + hi) / 2
        got = _base_at(mid_q)
        if got is None:
            # Too large / range break — search lower.
            hi = mid_q
            continue
        err = abs(got - base_out) / base_out
        if err <= q_tol_rel:
            return mid_q
        if got < base_out:
            lo = mid_q
        else:
            best_quote = mid_q
            hi = mid_q

    # Accept last overshooting quote if within a looser band, else fail.
    if best_quote is not None:
        got = _base_at(best_quote)
        if got is not None and abs(got - base_out) / base_out <= q_tol_rel * 10:
            return best_quote
    # Final check at hi (should be the first covering quote).
    got_hi = _base_at(hi)
    if got_hi is not None and abs(got_hi - base_out) / base_out <= q_tol_rel * 10:
        return hi
    return None
