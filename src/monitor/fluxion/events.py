"""Decode Fluxion AMM Swap logs and Limit Order Protocol fills."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from eth_abi import decode as abi_decode  # type: ignore[attr-defined]

from monitor.fluxion.abi import (
    TOPIC0_ORDER_FILLED,
    TOPIC0_V3_SWAP,
    TOPIC0_V3_SWAP_PCS,
    USDC_DECIMALS,
)
from monitor.fluxion.pools import PoolMeta
from monitor.quotes import FluxionRfqFillTick, FluxionSwapTick


def _topic_addr(topic: str) -> str:
    h = topic[2:] if topic.startswith("0x") else topic
    return "0x" + h[-40:].lower()


def _topic0(log: Mapping[str, Any]) -> str:
    topics = log.get("topics") or []
    if not topics:
        return ""
    t0 = topics[0]
    if isinstance(t0, bytes):
        return "0x" + t0.hex()
    return str(t0).lower()


def _hex_int(log: Mapping[str, Any], key: str) -> int:
    raw = log.get(key, "0x0")
    if isinstance(raw, str):
        return int(raw, 16)
    return int(raw or 0)


def decode_v3_swap_log(
    log: Mapping[str, Any],
    *,
    meta: PoolMeta,
    token0: str,
    token1: str,
    block_ts: int,
    recv_ts_ms: int,
    gas_used: int | None = None,
    effective_gas_price: int | None = None,
    gap: bool = False,
) -> FluxionSwapTick | None:
    """Decode a UniV3/PCS-style Swap log for a known pool."""
    t0 = _topic0(log)
    if t0 not in {TOPIC0_V3_SWAP.lower(), TOPIC0_V3_SWAP_PCS.lower()}:
        return None
    topics = log.get("topics") or []
    if len(topics) < 3:
        return None
    sender = _topic_addr(str(topics[1]))
    recipient = _topic_addr(str(topics[2]))
    data_hex = str(log.get("data") or "0x")
    raw = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x") else data_hex)
    try:
        if t0 == TOPIC0_V3_SWAP_PCS.lower():
            amount0, amount1, sqrt_price_x96, liquidity, tick, _pf0, _pf1 = abi_decode(
                ["int256", "int256", "uint160", "uint128", "int24", "uint128", "uint128"],
                raw,
            )
        else:
            amount0, amount1, sqrt_price_x96, liquidity, tick = abi_decode(
                ["int256", "int256", "uint160", "uint128", "int24"],
                raw,
            )
    except Exception:  # noqa: BLE001 - malformed log
        return None

    quote = meta.quote_token.lower()
    wrapper = meta.wrapper_token.lower()
    t0a, t1a = token0.lower(), token1.lower()
    # Decimals: quote=6, wrapper=18 for all inventory pools.
    if t0a == quote and t1a == wrapper:
        dec0, dec1 = USDC_DECIMALS, meta.wrapper_decimals
    elif t0a == wrapper and t1a == quote:
        dec0, dec1 = meta.wrapper_decimals, USDC_DECIMALS
    else:
        return None

    amount_token0 = Decimal(amount0) / Decimal(10**dec0)
    amount_token1 = Decimal(amount1) / Decimal(10**dec1)

    # Direction: trader buy_native when pool's wrapper balance decreases (amount < 0
    # for the wrapper side — Uniswap convention: positive amount = pool received).
    direction: str = "unknown"
    if t0a == wrapper:
        if amount0 < 0:
            direction = "buy_native"
        elif amount0 > 0:
            direction = "sell_native"
    elif t1a == wrapper:
        if amount1 < 0:
            direction = "buy_native"
        elif amount1 > 0:
            direction = "sell_native"

    price: Decimal | None = None
    try:
        from monitor.fluxion.pools import mid_from_sqrt_price_x96

        price = mid_from_sqrt_price_x96(
            int(sqrt_price_x96),
            token0_is_quote=(t0a == quote),
            token0_decimals=dec0,
            token1_decimals=dec1,
        )
    except (ValueError, ZeroDivisionError):
        price = None

    block_number = _hex_int(log, "blockNumber")
    log_index = _hex_int(log, "logIndex")
    tx_hash = str(log.get("transactionHash") or "")

    return FluxionSwapTick(
        pair_id=meta.pair_id,
        pool=meta.pool.lower(),
        block_number=block_number,
        block_ts=block_ts,
        recv_ts_ms=recv_ts_ms,
        tx_hash=tx_hash,
        log_index=log_index,
        sender=sender,
        recipient=recipient,
        amount0=int(amount0),
        amount1=int(amount1),
        sqrt_price_x96=int(sqrt_price_x96),
        liquidity=int(liquidity),
        tick=int(tick),
        amount_token0=amount_token0,
        amount_token1=amount_token1,
        direction=direction,  # type: ignore[arg-type]
        price_usdc_per_wrapper=price,
        gas_used=gas_used,
        effective_gas_price=effective_gas_price,
        gap=gap,
    )


def decode_lop_fill_log(
    log: Mapping[str, Any],
    *,
    block_ts: int,
    recv_ts_ms: int,
    gap: bool = False,
) -> FluxionRfqFillTick | None:
    """Decode OrderFilled(bytes32 orderHash, uint256 remainingMakingAmount)."""
    if _topic0(log) != TOPIC0_ORDER_FILLED.lower():
        return None
    data_hex = str(log.get("data") or "0x")
    raw = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x") else data_hex)
    topics = log.get("topics") or []
    try:
        # Some deployments index orderHash; others put both in data.
        if len(topics) >= 2 and len(raw) >= 32:
            order_hash = str(topics[1])
            if not order_hash.startswith("0x"):
                order_hash = "0x" + order_hash
            (remaining,) = abi_decode(["uint256"], raw)
        elif len(raw) >= 64:
            order_hash_bytes, remaining = abi_decode(["bytes32", "uint256"], raw)
            order_hash = "0x" + (
                order_hash_bytes.hex()
                if isinstance(order_hash_bytes, (bytes, bytearray))
                else bytes(order_hash_bytes).hex()
            )
        else:
            return None
    except Exception:  # noqa: BLE001
        return None

    block_number = _hex_int(log, "blockNumber")
    log_index = _hex_int(log, "logIndex")

    return FluxionRfqFillTick(
        block_number=block_number,
        block_ts=block_ts,
        recv_ts_ms=recv_ts_ms,
        tx_hash=str(log.get("transactionHash") or ""),
        log_index=log_index,
        order_hash=order_hash.lower(),
        remaining_making_amount=int(remaining),
        gap=gap,
    )


def swap_log_filter_topics() -> list[list[str]]:
    """topic0 OR filter for eth_getLogs (both UniV3 and PCS-style Swap)."""
    return [[TOPIC0_V3_SWAP, TOPIC0_V3_SWAP_PCS]]
