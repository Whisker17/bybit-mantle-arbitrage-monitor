"""Verified constants for the Mantle <> Bybit WMNT/USDT0 backtest.

Every address, fee and decimal here was read off-chain on 2026-07-28 and
cross-checked against Dune `dex.trades`. Do not "fix" these from memory.
"""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data"
REPORT = REPO / "report"


def _load_dotenv() -> None:
    """Read .env without adding a dependency. Real env always wins."""
    path = REPO / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# --- Mantle network ------------------------------------------------------
# MANTLE_RPC_URL in .env is a keyed, unthrottled endpoint. It is supplied as
# wss://wss-tob...; the same key works over https://rpc-tob..., which is what we
# want (plain request/response, no socket lifecycle to manage).
PUBLIC_RPC_URL = "https://rpc.mantle.xyz"


def _resolve_rpc_url() -> str:
    raw = os.environ.get("MANTLE_RPC_URL", "").strip()
    if not raw:
        return PUBLIC_RPC_URL
    if raw.startswith("wss://wss-"):
        return raw.replace("wss://wss-", "https://rpc-", 1)
    if raw.startswith("ws://ws-"):
        return raw.replace("ws://ws-", "http://rpc-", 1)
    return raw


RPC_URL = _resolve_rpc_url()
RPC_IS_KEYED = RPC_URL != PUBLIC_RPC_URL
CHAIN_ID = 5000
BLOCK_SECONDS = 2.0  # measured exactly 2.000s
BLOCKS_PER_DAY = int(86400 / BLOCK_SECONDS)  # 43_200

# Archive state reaches ~30 days on BOTH the public and the keyed endpoint
# (verified: 29d and 31d ok, 60d and 90d return empty). This is a property of
# Mantle's state pruning, not of the endpoint -- a faster RPC does not buy a
# longer window. 29 days keeps a margin against pruning advancing mid-run.
BACKFILL_DAYS = 29

# Public RPC hard-caps eth_getLogs at 10k blocks ("block range greater than
# 10000 max"). The keyed endpoint serves 200k spans, verified non-truncating
# (one 200k call == twenty 10k calls, byte-identical count). 100k is a
# deliberate margin under that.
GETLOGS_MAX_SPAN = 100_000 if RPC_IS_KEYED else 10_000

# Keyed endpoint sustained ~32 subcalls/s with zero errors at batch=20. We stay
# well under that: the user asked for modest traffic, and the whole job is only
# a few tens of thousands of calls.
RPC_MIN_INTERVAL = 0.05 if RPC_IS_KEYED else 0.35
RPC_HTTP_BATCH = 20 if RPC_IS_KEYED else 4

# The keyed endpoint is load-balanced and NOT read-your-writes consistent:
# eth_blockNumber can return a head that the node answering the next
# eth_getBlockByNumber has not ingested yet ("block N not found"). Back off from
# the tip so every block we touch is settled on every node.
HEAD_LAG_BLOCKS = 60  # 120s

MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"

# --- Tokens -------------------------------------------------------------
WMNT = "0x78c1b0c915c4faa5fffa6cabf0219da63d7f4cb8"
WMNT_DECIMALS = 18
# LayerZero OFT USDT0 -- NOT the legacy bridged USDT, and not the same asset
# as Bybit's USDT. Inventory rebalancing must traverse the OFT.
USDT0 = "0x779ded0c9e1022225f8e0630b35a9b54be713736"
USDT0_DECIMALS = 6


# --- Pools --------------------------------------------------------------
# Dune (7d): these two carry ~99% of real WMNT/USD* volume on Mantle.
# Everything else (FusionX, all USDC pools, legacy-USDT pools) is <$4K/7d.
class Pool:
    AGNI_V3 = "0x8fb12e957edbefd0105857fef675d621c8629a71"
    MOE_LB = "0xc0729cde19741de280b230d650f0fdad2ad79d09"


POOLS = {
    Pool.AGNI_V3: dict(
        name="agni_v3",
        kind="v3",
        # token0/token1 read from the pool, not assumed from address sort
        token0=USDT0,
        token0_decimals=USDT0_DECIMALS,
        token1=WMNT,
        token1_decimals=WMNT_DECIMALS,
        fee_bps=25.0,  # fee() = 2500 = 0.25%, continuous price
        volume_usd_7d=828_000,
    ),
    Pool.MOE_LB: dict(
        name="moe_lb",
        kind="lb",  # Liquidity Book v2.2, discrete bins
        token_x=WMNT,
        token_x_decimals=WMNT_DECIMALS,
        token_y=USDT0,
        token_y_decimals=USDT0_DECIMALS,
        bin_step=25,  # price grid is 25 bps per bin -- coarser than the
        # typical Bybit/DEX spread (~5 bps)
        base_fee_bps=25.0,  # baseFactor(10000) * binStep(25) => 0.25%
        # plus a time-decaying volatility surcharge we treat as zero (biases
        # profit UP, i.e. favourable to the arbitrageur -> upper bound)
        volume_usd_7d=601_000,
    ),
}

# --- Function selectors -------------------------------------------------
# Derived with keccak, not guessed (initial guesses reverted on-chain).
SEL = {
    # ERC20 / shared
    "decimals()": "0x313ce567",
    # Uniswap V3 style (Agni)
    "slot0()": "0x3850c7bd",
    "token0()": "0x0dfe1681",
    "token1()": "0xd21220a7",
    "fee()": "0xddca3f43",
    "liquidity()": "0x1a686502",
    # Liquidity Book v2.2 (Merchant Moe)
    "getTokenX()": "0x05e8746d",
    "getTokenY()": "0xda10610c",
    "getBinStep()": "0x17f11ecc",
    "getActiveId()": "0xdbe65edc",
    "getStaticFeeParameters()": "0x7ca0de30",
    "getVariableFeeParameters()": "0x8d7024e5",
    "getReserves()": "0x0902f1ac",
    "getBin(uint24)": "0x0abe9688",
    "getSwapOut(uint128,bool)": "0xe77366f8",
}

# --- Cost model ---------------------------------------------------------
BYBIT_TAKER_FEE = 0.0010  # 0.10% spot taker; 0.075% with MNT discount, we
# use the conservative undiscounted rate
COST_FLOOR_BPS = 35.0  # 25 (DEX) + 10 (Bybit taker), before slippage & gas

# Gas. Measured from real receipts on 2026-07-28: Agni swap gasUsed 138,350,
# Merchant Moe 212,307, both at ~75 gwei, total cost $0.004-0.007 per swap
# INCLUDING the L1 data fee. Against a 35 bps floor this is ~0.05 bps at $1K --
# a rounding error, included for completeness rather than because it matters.
GAS_UNITS = {"agni_v3": 138_350, "moe_lb": 212_307}
L1_FEE_USD = 0.002  # OP-Stack L1 data fee, measured; not visible in gasUsed

# Inventory. The strategy is not a flash loan: capital must sit pre-positioned
# on BOTH venues, so each leg fires simultaneously and nothing is bridged in the
# critical path. Carrying cost is therefore a cost of the WINDOW, not of an
# individual fill -- amortising a continuous funding rate onto discrete events
# would be arbitrary. M5 charges it at the aggregate level instead.
INVENTORY_APR = 0.10  # opportunity cost of deployed capital
# Capital needed to run one direction at size Q: Q of quote on one venue and
# Q-worth of base on the other.
INVENTORY_CAPITAL_MULT = 2.0

# A Bybit quote inferred from a print this far before the DEX block is not
# evidence about the book at that block. Rows past it are flagged, never
# silently dropped.
MAX_BYBIT_LAG_MS = 60_000

# Notional ladder, USD. Profit is concave in size (revenue linear, slippage
# superlinear) so an interior optimum Q* exists; M4 also solves for it.
SIZE_LADDER_USD = [1_000, 5_000, 10_000, 25_000, 50_000, 100_000]

# --- Bybit --------------------------------------------------------------
BYBIT_SYMBOL = "MNTUSDT"
BYBIT_TICK_URL = "https://public.bybit.com/spot/{sym}/{sym}_{date}.csv.gz"
BYBIT_REST = "https://api.bybit.com"
