"""Selectors and topic0 constants for Fluxion V3 + Limit Order Protocol."""

from __future__ import annotations

from eth_utils import keccak  # type: ignore[attr-defined]

# Function selectors (first 4 bytes of keccak).
SEL_SLOT0 = "0x3850c7bd"
SEL_LIQUIDITY = "0x1a686502"
SEL_TOKEN0 = "0x0dfe1681"
SEL_TOKEN1 = "0xd21220a7"
SEL_DECIMALS = "0x313ce567"
SEL_CONVERT_TO_ASSETS = "0x07a2d13a"
SEL_ASSET = "0x38d52e0f"

# Uniswap V3 Swap (vanilla) — Fluxion documents UniV3 lineage; re-verify live.
TOPIC0_V3_SWAP = "0x" + keccak(
    text="Swap(address,address,int256,int256,uint160,uint128,int24)"
).hex()

# PancakeSwap-style V3 Swap with protocolFees (Agni trap from phase-1) — accept either.
TOPIC0_V3_SWAP_PCS = "0x" + keccak(
    text="Swap(address,address,int256,int256,uint160,uint128,int24,uint128,uint128)"
).hex()

SWAP_TOPIC0S = frozenset({TOPIC0_V3_SWAP, TOPIC0_V3_SWAP_PCS})

# 1inch LOP v4 — signature-derived (M1); not fill-observed yet.
TOPIC0_ORDER_FILLED = "0x" + keccak(text="OrderFilled(bytes32,uint256)").hex()
TOPIC0_ORDER_CANCELLED = "0x" + keccak(text="OrderCancelled(bytes32)").hex()

USDC_DECIMALS = 6
WRAPPER_DECIMALS_DEFAULT = 18
NATIVE_DECIMALS_DEFAULT = 18
