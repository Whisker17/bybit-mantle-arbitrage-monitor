"""Per-DEX encoding/decoding. Isolated here so M2 stays a driver loop.

Both venues are quoted through their own on-chain quote path rather than a
reimplementation of their swap math. That is the whole reason this POC can be
Python: we never port tick or bin arithmetic, we ask the contract.

Verified on-chain 2026-07-28:
  - Agni is a PancakeSwap-V3-lineage fork. Its QuoterV2 takes the Uniswap
    QuoterV2 *struct* form; the flat Quoter-V1 form reverts.
  - AgniFactory.getPool(USDT0, WMNT, 2500) == the pool address in config.
  - Merchant Moe LBPair.getSwapOut charges exactly 0.25% on the input side and
    currently adds no volatility surcharge.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

from .config import SEL, USDT0, USDT0_DECIMALS, WMNT, WMNT_DECIMALS, Pool
from .rpc import cs, encode_call

# Verified: AgniFactory 0x25780dc8... getPool(USDT0, WMNT, 2500) -> Pool.AGNI_V3
AGNI_FACTORY = "0x25780dc8fc3cfbd75f33bfdab65e969b603b2035"
AGNI_QUOTER_V2 = "0xc4aadc921e1cdb66c5300bc158a313292923c0cb"
AGNI_FEE = 2500  # 0.25%

QUOTE_EXACT_INPUT_SINGLE = "0x" + keccak(
    text="quoteExactInputSingle((address,address,uint256,uint24,uint160))"
).hex()[:8]

# Q128.128-ish price ladder base for Liquidity Book bins.
LB_BIN_MID = 2 ** 23


@dataclass(frozen=True)
class Quote:
    ok: bool
    amount_out: int          # raw units of the output token
    amount_in_left: int      # >0 means the pool could not fill the full size
    gas_estimate: int


# --- state reads --------------------------------------------------------
def state_calls(pool: str, kind: str) -> list[tuple[str, bytes]]:
    if kind == "v3":
        return [(pool, encode_call(SEL["slot0()"])),
                (pool, encode_call(SEL["liquidity()"]))]
    return [(pool, encode_call(SEL["getActiveId()"])),
            (pool, encode_call(SEL["getStaticFeeParameters()"])),
            (pool, encode_call(SEL["getVariableFeeParameters()"]))]


def decode_state(kind: str, rets: list[tuple[bool, bytes]], bin_step: int) -> dict:
    """Return {mid_price (USDT0 per MNT), plus venue-specific diagnostics}."""
    if kind == "v3":
        ok0, d0 = rets[0]
        if not ok0 or len(d0) < 64:
            return {}
        sqrt_price_x96 = int.from_bytes(d0[:32], "big")
        tick = int.from_bytes(d0[32:64], "big", signed=True)
        # token0=USDT0(6dec), token1=WMNT(18dec). sqrtPriceX96^2/2^192 is raw
        # token1/token0; dividing by 10**(18-6) gives human WMNT per USDT0.
        raw = (sqrt_price_x96 / 2 ** 96) ** 2
        wmnt_per_usdt0 = raw / 10 ** (WMNT_DECIMALS - USDT0_DECIMALS)
        liq = int.from_bytes(rets[1][1][:32], "big") if rets[1][0] else None
        return {"mid_price": 1.0 / wmnt_per_usdt0,
                "sqrt_price_x96": sqrt_price_x96, "tick": tick,
                "liquidity": liq}

    ok0, d0 = rets[0]
    if not ok0 or len(d0) < 32:
        return {}
    active_id = int.from_bytes(d0[:32], "big")
    # LB bin price: (1 + binStep/1e4)^(id - 2^23) in raw Y-per-X terms.
    raw = (1 + bin_step / 10_000) ** (active_id - LB_BIN_MID)
    mid = raw * 10 ** (WMNT_DECIMALS - USDT0_DECIMALS)
    out = {"mid_price": mid, "active_id": active_id}
    if rets[2][0] and len(rets[2][1]) >= 64:
        # (volatilityAccumulator, volatilityReference, idReference, timeOfLastUpdate)
        vals = abi_decode(["uint24", "uint24", "uint24", "uint40"], rets[2][1])
        out["volatility_accumulator"] = vals[0]
        out["volatility_reference"] = vals[1]
    return out


# --- quote encoding -----------------------------------------------------
def quote_call(pool: str, kind: str, token_in: str, amount_in: int) -> tuple[str, bytes]:
    if kind == "v3":
        token_out = WMNT if token_in.lower() == USDT0.lower() else USDT0
        payload = abi_encode(
            ["(address,address,uint256,uint24,uint160)"],
            [(cs(token_in), cs(token_out), amount_in, AGNI_FEE, 0)],
        )
        return (AGNI_QUOTER_V2,
                bytes.fromhex(QUOTE_EXACT_INPUT_SINGLE[2:]) + payload)
    # LB: swapForY=True means tokenX(WMNT) in, tokenY(USDT0) out.
    swap_for_y = token_in.lower() == WMNT.lower()
    return (pool, encode_call(SEL["getSwapOut(uint128,bool)"],
                              ["uint128", "bool"], [amount_in, swap_for_y]))


def decode_quote(kind: str, ok: bool, data: bytes) -> Quote:
    if not ok:
        return Quote(False, 0, 0, 0)
    try:
        if kind == "v3":
            # (amountOut, sqrtPriceX96After, initializedTicksCrossed, gasEstimate)
            out, _, _, gas = abi_decode(
                ["uint256", "uint160", "uint32", "uint256"], data)
            return Quote(True, out, 0, gas)
        left, out, _fees = abi_decode(["uint128", "uint128", "uint128"], data)
        return Quote(True, out, left, 0)
    except Exception:  # noqa: BLE001 - a malformed return is just a failed quote
        return Quote(False, 0, 0, 0)


def token_in_for(direction: str) -> str:
    """direction A = buy MNT on-chain (USDT0 in); B = sell MNT on-chain."""
    return USDT0 if direction == "A" else WMNT


def decimals_of(token: str) -> int:
    return USDT0_DECIMALS if token.lower() == USDT0.lower() else WMNT_DECIMALS


VENUE_POOLS = {Pool.AGNI_V3: "v3", Pool.MOE_LB: "lb"}
