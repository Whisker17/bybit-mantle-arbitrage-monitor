"""Seams: pure TVL valuation + low-liquidity resolution (WHI-782)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from monitor.fluxion.abi import SEL_BALANCE_OF
from monitor.fluxion.pools import PoolMeta
from monitor.fluxion.rpc import encode_call
from monitor.fluxion.tvl import (
    compute_pool_tvl_usd,
    decode_pool_tvl,
    human_balance,
    is_low_liquidity,
    pool_tvl_balance_calls,
)


def test_human_balance() -> None:
    assert human_balance(1_500_000, 6) == Decimal("1.5")
    assert human_balance(10**18, 18) == Decimal("1")


def test_compute_pool_tvl_usd() -> None:
    # 10 base @ $250 + 1000 quote @ $1 = 3500
    tvl = compute_pool_tvl_usd(
        base_balance=Decimal("10"),
        quote_balance=Decimal("1000"),
        mid_quote_per_base=Decimal("250"),
    )
    assert tvl == Decimal("3500")


def test_compute_pool_tvl_rejects_negative() -> None:
    with pytest.raises(ValueError):
        compute_pool_tvl_usd(
            base_balance=Decimal("-1"),
            quote_balance=Decimal("0"),
            mid_quote_per_base=Decimal("1"),
        )


def test_is_low_liquidity_live_tvl_overrides_inventory() -> None:
    # Inventory said high-liq, but live TVL is under threshold.
    assert is_low_liquidity(
        tvl_usd=Decimal("10_000"),
        threshold_usd=Decimal("50_000"),
        inventory_low=False,
        has_amm=True,
    )
    # Live TVL above threshold clears a stale inventory low flag.
    assert not is_low_liquidity(
        tvl_usd=Decimal("80_000"),
        threshold_usd=Decimal("50_000"),
        inventory_low=True,
        has_amm=True,
    )


def test_is_low_liquidity_fallback_and_no_amm() -> None:
    assert is_low_liquidity(
        tvl_usd=None,
        threshold_usd=Decimal("50_000"),
        inventory_low=True,
        has_amm=True,
    )
    assert not is_low_liquidity(
        tvl_usd=None,
        threshold_usd=Decimal("50_000"),
        inventory_low=False,
        has_amm=True,
    )
    assert is_low_liquidity(
        tvl_usd=Decimal("1_000_000"),
        threshold_usd=Decimal("50_000"),
        inventory_low=False,
        has_amm=False,
    )


def test_balance_of_selector() -> None:
    data = encode_call(SEL_BALANCE_OF, ["address"], ["0x" + "11" * 20])
    assert data[:4].hex() == "70a08231"


def test_decode_pool_tvl_tick() -> None:
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
    # 2 wrapper (18d) + 500 USDC (6d), mid = 250 → TVL = 2*250 + 500 = 1000
    base_raw = (2 * 10**18).to_bytes(32, "big")
    quote_raw = (500 * 10**6).to_bytes(32, "big")
    tick = decode_pool_tvl(
        meta,
        [(True, base_raw), (True, quote_raw)],
        mid_quote_per_base=Decimal("250"),
        block_number=99,
        block_ts=1_700_000_000,
        recv_ts_ms=1_700_000_000_500,
    )
    assert tick.pair_id == "TSLAx"
    assert tick.pool == pool.lower()
    assert tick.base_bal == Decimal("2")
    assert tick.quote_bal == Decimal("500")
    assert tick.tvl_usd == Decimal("1000")
    assert tick.base_price == Decimal("250")
    assert tick.block_number == 99


def test_pool_tvl_balance_calls_order() -> None:
    meta = PoolMeta(
        pair_id="X",
        pool="0x" + "aa" * 20,
        wrapper_token="0x" + "bb" * 20,
        native_token="0x" + "cc" * 20,
        quote_token="0x" + "dd" * 20,
    )
    calls = pool_tvl_balance_calls(meta)
    assert len(calls) == 2
    assert calls[0][0].lower() == meta.wrapper_token.lower()
    assert calls[1][0].lower() == meta.quote_token.lower()
