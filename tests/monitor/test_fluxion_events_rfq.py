"""Seams: Swap/LOP log decode + RFQ response parse (incl. 204)."""

from __future__ import annotations

from decimal import Decimal

from eth_abi import encode as abi_encode
from eth_utils import keccak

from monitor.fluxion.abi import TOPIC0_ORDER_FILLED, TOPIC0_V3_SWAP
from monitor.fluxion.events import decode_lop_fill_log, decode_v3_swap_log
from monitor.fluxion.pools import PoolMeta
from monitor.fluxion.rfq import parse_rfq_response


def test_parse_rfq_200() -> None:
    tick = parse_rfq_response(
        pair_id="TSLAx",
        token_in="0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9",
        token_out="0x8ad3c73f833d3f9a523ab01476625f269aeb7cf0",
        amount_in="100000000",
        poll_ts_ms=1,
        recv_ts_ms=2,
        http_status=200,
        body={
            "side": "buy",
            "amountOut": "400000000000000000",
            "price": "250.0",
            "requestId": "uuid-1",
        },
    )
    assert tick.available is True
    assert tick.price == Decimal("250.0")
    assert tick.request_id == "uuid-1"


def test_parse_rfq_204_unavailable() -> None:
    tick = parse_rfq_response(
        pair_id="TSLAx",
        token_in="0x09",
        token_out="0x08",
        amount_in="100000000",
        poll_ts_ms=1,
        recv_ts_ms=2,
        http_status=204,
        body=None,
    )
    assert tick.available is False
    assert tick.price is None
    assert tick.http_status == 204


def test_decode_v3_swap_log() -> None:
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
    # amount0 (USDC) > 0 pool received quote; amount1 (wrapper) < 0 pool sent wrapper
    # → buy_native
    amount0 = 250 * 10**6
    amount1 = -(10**18)
    sqrt = 2**96
    data = abi_encode(
        ["int256", "int256", "uint160", "uint128", "int24"],
        [amount0, amount1, sqrt, 10**18, 0],
    )
    sender = "0x" + "11" * 20
    recipient = "0x" + "22" * 20
    log = {
        "address": pool,
        "topics": [
            TOPIC0_V3_SWAP,
            "0x" + "0" * 24 + sender[2:],
            "0x" + "0" * 24 + recipient[2:],
        ],
        "data": "0x" + data.hex(),
        "blockNumber": hex(100),
        "logIndex": hex(3),
        "transactionHash": "0x" + "ab" * 32,
    }
    tick = decode_v3_swap_log(
        log,
        meta=meta,
        token0=quote,
        token1=wrapper,
        block_ts=1_700_000_000,
        recv_ts_ms=1_700_000_000_100,
        gas_used=140_000,
        effective_gas_price=75_000_000_000,
    )
    assert tick is not None
    assert tick.direction == "buy_native"
    assert tick.gas_used == 140_000
    assert tick.effective_gas_price == 75_000_000_000
    assert tick.log_index == 3
    assert tick.sender == sender


def test_decode_lop_fill() -> None:
    order_hash = bytes.fromhex("ee" * 32)
    remaining = 0
    data = abi_encode(["bytes32", "uint256"], [order_hash, remaining])
    log = {
        "topics": [TOPIC0_ORDER_FILLED],
        "data": "0x" + data.hex(),
        "blockNumber": "0x64",
        "logIndex": "0x1",
        "transactionHash": "0x" + "cd" * 32,
    }
    tick = decode_lop_fill_log(log, block_ts=1, recv_ts_ms=2)
    assert tick is not None
    assert tick.order_hash == "0x" + "ee" * 32
    assert tick.remaining_making_amount == 0
    # topic0 constant matches keccak of signature
    assert TOPIC0_ORDER_FILLED == "0x" + keccak(
        text="OrderFilled(bytes32,uint256)"
    ).hex()
