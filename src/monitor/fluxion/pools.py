"""Fluxion V3 pool state via Multicall3 + ERC-4626 wrapper conversion."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from monitor.fluxion.abi import (
    NATIVE_DECIMALS_DEFAULT,
    SEL_CONVERT_TO_ASSETS,
    SEL_LIQUIDITY,
    SEL_SLOT0,
    SEL_TOKEN0,
    SEL_TOKEN1,
    USDC_DECIMALS,
    WRAPPER_DECIMALS_DEFAULT,
)
from monitor.fluxion.rpc import Rpc, encode_call
from monitor.quotes import FluxionPoolStateTick


@dataclass(frozen=True, slots=True)
class PoolMeta:
    pair_id: str
    pool: str
    wrapper_token: str
    native_token: str
    quote_token: str  # quote ERC-20 address (USDC Mantle / USDT BSC)
    native_decimals: int = NATIVE_DECIMALS_DEFAULT
    wrapper_decimals: int = WRAPPER_DECIMALS_DEFAULT
    quote_decimals: int = USDC_DECIMALS
    # Fluxion uses ERC-4626 wrapper; Pancake bStocks trade native raw (no wrap).
    has_erc4626_wrapper: bool = True


def mid_from_sqrt_price_x96(
    sqrt_price_x96: int,
    *,
    token0_is_quote: bool,
    token0_decimals: int,
    token1_decimals: int,
) -> Decimal:
    """Return human mid as **quote per base** where base is the non-quote token.

    For Fluxion xStock pools the non-quote token is the wrapper.
    """
    if sqrt_price_x96 <= 0:
        raise ValueError("sqrt_price_x96 must be positive")
    # raw = token1/token0 in base units
    ratio = (Decimal(sqrt_price_x96) / Decimal(2**96)) ** 2
    # human token1 per token0 (Decimal power keeps negative exponents exact)
    t1_per_t0 = ratio * (Decimal(10) ** (token0_decimals - token1_decimals))
    if token0_is_quote:
        # token0=quote, token1=wrapper → t1_per_t0 = wrapper per quote;
        # want quote per wrapper.
        if t1_per_t0 == 0:
            raise ValueError("zero price")
        return Decimal(1) / t1_per_t0
    # token0=wrapper, token1=quote → t1_per_t0 = quote per wrapper
    return t1_per_t0


def decode_slot0(data: bytes) -> tuple[int, int]:
    """Return (sqrtPriceX96, tick) from slot0 return data."""
    if len(data) < 64:
        raise ValueError("slot0 return too short")
    sqrt_price_x96 = int.from_bytes(data[0:32], "big")
    tick = int.from_bytes(data[32:64], "big", signed=True)
    return sqrt_price_x96, tick


def decode_uint(data: bytes) -> int:
    if len(data) < 32:
        raise ValueError("uint return too short")
    return int.from_bytes(data[0:32], "big")


def decode_address(data: bytes) -> str:
    if len(data) < 32:
        raise ValueError("address return too short")
    return "0x" + data[-20:].hex()


def pool_state_calls(pool: str) -> list[tuple[str, bytes]]:
    return [
        (pool, encode_call(SEL_SLOT0)),
        (pool, encode_call(SEL_LIQUIDITY)),
        (pool, encode_call(SEL_TOKEN0)),
        (pool, encode_call(SEL_TOKEN1)),
    ]


def decode_pool_state(
    meta: PoolMeta,
    rets: list[tuple[bool, bytes]],
    *,
    block_number: int,
    block_ts: int,
    recv_ts_ms: int,
    assets_per_share_raw: int | None = None,
    gap: bool = False,
) -> FluxionPoolStateTick:
    """Decode Multicall3 results for one pool into a state tick."""
    if len(rets) < 4:
        raise ValueError("need slot0, liquidity, token0, token1 results")
    ok0, d0 = rets[0]
    ok1, d1 = rets[1]
    ok2, d2 = rets[2]
    ok3, d3 = rets[3]
    if not (ok0 and ok1 and ok2 and ok3):
        raise ValueError(f"pool state call failed for {meta.pair_id}")

    sqrt_price_x96, tick = decode_slot0(d0)
    liquidity = decode_uint(d1)
    token0 = decode_address(d2).lower()
    token1 = decode_address(d3).lower()
    quote = meta.quote_token.lower()
    wrapper = meta.wrapper_token.lower()

    if token0 == quote and token1 == wrapper:
        token0_is_quote = True
        t0_dec, t1_dec = meta.quote_decimals, meta.wrapper_decimals
    elif token0 == wrapper and token1 == quote:
        token0_is_quote = False
        t0_dec, t1_dec = meta.wrapper_decimals, meta.quote_decimals
    else:
        raise ValueError(
            f"{meta.pair_id}: pool tokens {token0}/{token1} do not match "
            f"wrapper={wrapper} quote={quote}"
        )

    mid_wrapper = mid_from_sqrt_price_x96(
        sqrt_price_x96,
        token0_is_quote=token0_is_quote,
        token0_decimals=t0_dec,
        token1_decimals=t1_dec,
    )

    if assets_per_share_raw is None:
        # 1:1 fallback if conversion not provided (should be rare).
        assets_per_share = Decimal(1)
    else:
        assets_per_share = Decimal(assets_per_share_raw) / Decimal(
            10**meta.native_decimals
        )
        # convertToAssets(1e wrapper_decimals) → native raw for 1 share unit
        # human native per 1 human wrapper share:
        # raw_assets / 10**native_dec  (since we passed 10**wrapper_dec shares)
        if assets_per_share <= 0:
            raise ValueError("wrapper assets_per_share must be positive")

    mid_native = mid_wrapper / assets_per_share

    return FluxionPoolStateTick(
        pair_id=meta.pair_id,
        pool=meta.pool.lower(),
        block_number=block_number,
        block_ts=block_ts,
        recv_ts_ms=recv_ts_ms,
        sqrt_price_x96=sqrt_price_x96,
        tick=tick,
        liquidity=liquidity,
        token0=token0,
        token1=token1,
        mid_usdc_per_wrapper=mid_wrapper,
        mid_usdc_per_native=mid_native,
        wrapper_assets_per_share=assets_per_share,
        gap=gap,
    )


def fetch_pool_states(
    rpc: Rpc,
    pools: Iterable[PoolMeta],
    *,
    block_number: int,
    block_ts: int,
    recv_ts_ms: int,
    gap: bool = False,
) -> list[FluxionPoolStateTick]:
    """One Multicall3 round-trip for all pool slot0s (+ optional ERC-4626 convert)."""
    pool_list = list(pools)
    if not pool_list:
        return []

    # Mixed wrapper/non-wrapper batches: encode convert only where needed and
    # track per-pool result slices (not a fixed stride).
    calls: list[tuple[str, bytes]] = []
    slices: list[tuple[int, int, bool]] = []  # (start, end, has_convert)
    for meta in pool_list:
        start = len(calls)
        calls.extend(pool_state_calls(meta.pool))
        if meta.has_erc4626_wrapper:
            calls.append(
                (
                    meta.wrapper_token,
                    encode_call(
                        SEL_CONVERT_TO_ASSETS,
                        ["uint256"],
                        [10**meta.wrapper_decimals],
                    ),
                )
            )
            slices.append((start, len(calls), True))
        else:
            slices.append((start, len(calls), False))

    rets = rpc.multicall(calls, block=block_number, allow_failure=True)
    out: list[FluxionPoolStateTick] = []
    for meta, (start, end, has_convert) in zip(pool_list, slices, strict=True):
        chunk = rets[start:end]
        assets_raw: int | None = None
        if has_convert and len(chunk) >= 5 and chunk[4][0] and chunk[4][1]:
            try:
                assets_raw = decode_uint(chunk[4][1])
            except ValueError:
                assets_raw = None
        elif not has_convert:
            # 1 human native per 1 "wrapper" unit (identity — no vault).
            assets_raw = 10**meta.native_decimals
        try:
            out.append(
                decode_pool_state(
                    meta,
                    chunk[:4],
                    block_number=block_number,
                    block_ts=block_ts,
                    recv_ts_ms=recv_ts_ms,
                    assets_per_share_raw=assets_raw,
                    gap=gap,
                )
            )
        except ValueError:
            # Skip failed pools; daemon logs at higher level if counts drop.
            continue
    return out
