"""Seams: V3 mid from sqrtPriceX96, wrapper conversion, pool state decode."""

from __future__ import annotations

from decimal import Decimal

from monitor.fluxion.abi import SEL_SLOT0
from monitor.fluxion.pools import (
    PoolMeta,
    decode_pool_state,
    decode_slot0,
    mid_from_sqrt_price_x96,
)
from monitor.fluxion.rpc import encode_call


def test_mid_token0_is_quote() -> None:
    # sqrtPriceX96 = 2**96 → raw token1/token0 = 1
    # token0=USDC(6), token1=wrapper(18) → human wrapper per USDC = 1e-12
    # quote per wrapper = 1e12
    mid = mid_from_sqrt_price_x96(
        2**96,
        token0_is_quote=True,
        token0_decimals=6,
        token1_decimals=18,
    )
    assert mid == Decimal("1000000000000")


def test_mid_token0_is_wrapper() -> None:
    # token0=wrapper(18), token1=USDC(6), sqrt=2**96 → human USDC per wrapper = 1e12
    mid = mid_from_sqrt_price_x96(
        2**96,
        token0_is_quote=False,
        token0_decimals=18,
        token1_decimals=6,
    )
    assert mid == Decimal("1000000000000")


def test_decode_slot0() -> None:
    # pack sqrt and tick=0
    data = (2**96).to_bytes(32, "big") + (0).to_bytes(32, "big", signed=True)
    sqrt, tick = decode_slot0(data)
    assert sqrt == 2**96
    assert tick == 0


def test_decode_pool_state_with_1_to_1_wrapper() -> None:
    quote = "0x09bc4e0d864854c6afb6eb9a9cdf58ac190d0df9"
    wrapper = "0x43680abf18cf54898be84c6ef78237cfbd441883"
    pool = "0x5e7935d70b5d14b6cf36fbde59944533fab96b3c"
    meta = PoolMeta(
        pair_id="TSLAx",
        pool=pool,
        wrapper_token=wrapper,
        native_token="0x8ad3c73f833d3f9a523ab01476625f269aeb7cf0",
        quote_token=quote,
    )
    # token0=quote, token1=wrapper, mid_wrapper huge as above — use a realistic-ish
    # sqrt so mid is finite: craft via known formula is heavy; use token0=wrapper path
    # with sqrt such that human quote/wrapper = 250.
    # t1_per_t0_human = 250 = raw * 10**(18-6) = raw * 1e12
    # raw = 250 / 1e12 = 2.5e-10
    # sqrt = sqrt(raw) * 2**96
    import math

    raw = 250 / 1e12
    sqrt_price_x96 = int(math.sqrt(raw) * (2**96))
    slot0 = sqrt_price_x96.to_bytes(32, "big") + (0).to_bytes(32, "big", signed=True)
    liq = (10**18).to_bytes(32, "big")
    t0 = bytes(12) + bytes.fromhex(wrapper[2:])
    t1 = bytes(12) + bytes.fromhex(quote[2:])
    rets = [
        (True, slot0),
        (True, liq),
        (True, t0),
        (True, t1),
    ]
    # 1 share → 1e18 native raw (1:1)
    tick = decode_pool_state(
        meta,
        rets,
        block_number=42,
        block_ts=1_700_000_000,
        recv_ts_ms=1_700_000_000_100,
        assets_per_share_raw=10**18,
    )
    assert tick.pair_id == "TSLAx"
    assert tick.block_number == 42
    assert abs(tick.mid_usdc_per_wrapper - Decimal("250")) < Decimal("0.5")
    assert abs(tick.mid_usdc_per_native - Decimal("250")) < Decimal("0.5")
    assert tick.wrapper_assets_per_share == Decimal("1")


def test_encode_call_slot0_selector() -> None:
    data = encode_call(SEL_SLOT0)
    assert data.hex() == "3850c7bd"
