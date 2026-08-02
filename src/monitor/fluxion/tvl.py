"""DEX pool TVL from ERC-20 balanceOf + AMM mid (WHI-782).

V3 concentrated-liquidity pools report a virtual ``liquidity`` (L) in the
current tick range — that is **not** dollar TVL. Inventory YAML
``est_liquidity_usd`` is a static snapshot. Live TVL is:

    TVL_usd = base_balance × mid_quote_per_base + quote_balance × 1

where quote is USDC/USDT (~$1) and mid is the pool AMM mid for the base
token held in the pool (wrapper share for Fluxion ERC-4626 pools; native
for Pancake bStocks).

TVL is a **capital-size** metric, not tradeable depth. Bucket depth lives
in PnL v2 (DESIGN §2.6); do not equate the two.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal

from monitor.fluxion.abi import SEL_BALANCE_OF
from monitor.fluxion.pools import PoolMeta, decode_uint
from monitor.fluxion.rpc import Rpc, encode_call
from monitor.quotes import DexPoolTvlTick


def human_balance(raw: int, decimals: int) -> Decimal:
    """Convert raw ERC-20 units to human amount."""
    if raw < 0:
        raise ValueError("balance raw must be non-negative")
    if decimals < 0:
        raise ValueError("decimals must be non-negative")
    return Decimal(raw) / (Decimal(10) ** decimals)


def compute_pool_tvl_usd(
    *,
    base_balance: Decimal,
    quote_balance: Decimal,
    mid_quote_per_base: Decimal,
    quote_price_usd: Decimal = Decimal(1),
) -> Decimal:
    """Dollar TVL from human balances and quote-per-base mid.

    ``quote_price_usd`` defaults to 1 (USDC/USDT). Mid is the pool's
    quote-per-base price for the token actually held as base in the pool
    (wrapper mid on Fluxion; native mid on Pancake).
    """
    if mid_quote_per_base < 0 or quote_price_usd < 0:
        raise ValueError("prices must be non-negative")
    if base_balance < 0 or quote_balance < 0:
        raise ValueError("balances must be non-negative")
    return base_balance * mid_quote_per_base + quote_balance * quote_price_usd


def is_low_liquidity(
    *,
    tvl_usd: Decimal | None,
    threshold_usd: Decimal,
    inventory_low: bool,
    has_amm: bool,
) -> bool:
    """Dynamic low-liq flag: live TVL when present, else inventory fallback.

    Pairs without an AMM pool are always low-liquidity. When TVL has not
    been sampled yet, keep the inventory-time ``low_liquidity`` bit so the
    UI does not flip on cold start.
    """
    if threshold_usd <= 0:
        raise ValueError("threshold_usd must be positive")
    if not has_amm:
        return True
    if tvl_usd is not None:
        return tvl_usd < threshold_usd
    return inventory_low


def balance_of_call(token: str, holder: str) -> tuple[str, bytes]:
    """ERC-20 ``balanceOf(holder)`` call targeting ``token``."""
    from monitor.fluxion.rpc import cs

    return (
        token,
        encode_call(SEL_BALANCE_OF, ["address"], [cs(holder)]),
    )


def pool_tvl_balance_calls(meta: PoolMeta) -> list[tuple[str, bytes]]:
    """Two balanceOf calls: base (wrapper/native) then quote, held by pool."""
    pool = meta.pool
    return [
        balance_of_call(meta.wrapper_token, pool),
        balance_of_call(meta.quote_token, pool),
    ]


def decode_pool_tvl(
    meta: PoolMeta,
    rets: list[tuple[bool, bytes]],
    *,
    mid_quote_per_base: Decimal,
    block_number: int,
    block_ts: int,
    recv_ts_ms: int,
    gap: bool = False,
) -> DexPoolTvlTick:
    """Decode balanceOf pair + mid into a journal tick."""
    if len(rets) < 2:
        raise ValueError("need base + quote balanceOf results")
    ok0, d0 = rets[0]
    ok1, d1 = rets[1]
    if not (ok0 and ok1):
        raise ValueError(f"balanceOf failed for {meta.pair_id}")
    base_raw = decode_uint(d0)
    quote_raw = decode_uint(d1)
    base_bal = human_balance(base_raw, meta.wrapper_decimals)
    quote_bal = human_balance(quote_raw, meta.quote_decimals)
    tvl = compute_pool_tvl_usd(
        base_balance=base_bal,
        quote_balance=quote_bal,
        mid_quote_per_base=mid_quote_per_base,
    )
    return DexPoolTvlTick(
        pair_id=meta.pair_id,
        pool=meta.pool.lower(),
        block_number=block_number,
        block_ts=block_ts,
        recv_ts_ms=recv_ts_ms,
        base_bal=base_bal,
        quote_bal=quote_bal,
        base_price=mid_quote_per_base,
        tvl_usd=tvl,
        gap=gap,
    )


def fetch_pool_tvls(
    rpc: Rpc,
    pools: Iterable[PoolMeta],
    mid_by_pair: Mapping[str, Decimal],
    *,
    block_number: int,
    block_ts: int,
    recv_ts_ms: int,
    gap: bool = False,
) -> list[DexPoolTvlTick]:
    """One Multicall3 for all pool balanceOf pairs; value with provided mids.

    Pools missing a mid in ``mid_by_pair`` are skipped (caller should force
    a pool-state sample when TVL is due).
    """
    pool_list = [p for p in pools if p.pair_id in mid_by_pair]
    if not pool_list:
        return []

    calls: list[tuple[str, bytes]] = []
    for meta in pool_list:
        calls.extend(pool_tvl_balance_calls(meta))

    rets = rpc.multicall(calls, block=block_number, allow_failure=True)
    out: list[DexPoolTvlTick] = []
    for i, meta in enumerate(pool_list):
        chunk = rets[i * 2 : i * 2 + 2]
        mid = mid_by_pair[meta.pair_id]
        try:
            out.append(
                decode_pool_tvl(
                    meta,
                    chunk,
                    mid_quote_per_base=mid,
                    block_number=block_number,
                    block_ts=block_ts,
                    recv_ts_ms=recv_ts_ms,
                    gap=gap,
                )
            )
        except ValueError:
            continue
    return out
