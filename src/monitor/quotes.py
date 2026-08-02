"""Shared quote / trade tick types for live collectors (M2 / WHI-731).

Downstream metrics (M3) and attribution (M4) should depend on these shapes,
not on Bybit WS or Fluxion RPC client internals (DESIGN §4.3).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


def now_ms() -> int:
    """Wall-clock milliseconds for recv timestamps and gap windows."""
    return int(time.time() * 1000)


@dataclass(frozen=True, slots=True)
class BybitBookTick:
    """Best bid/ask from Bybit public WS, with multiplier already applied."""

    pair_id: str
    symbol: str
    exchange_ts_ms: int
    recv_ts_ms: int
    bid: Decimal
    ask: Decimal
    bid_de_multiplied: Decimal
    ask_de_multiplied: Decimal
    multiplier: Decimal
    gap: bool = False




@dataclass(frozen=True, slots=True)
class BybitDepthTick:
    """Throttled Bybit depth snapshot with precomputed de-multiplied VWAPs (WHI-755).

    ``bid_vwap_dm[i]`` / ``ask_vwap_dm[i]`` are effective fill prices at
    ``buckets_usd[i]`` notional (None = unfillable). Prices and notionals use
    de-multiplied units so consumers need no further multiplier math.
    """

    pair_id: str
    symbol: str
    exchange_ts_ms: int
    recv_ts_ms: int
    bid: Decimal
    ask: Decimal
    bid_de_multiplied: Decimal
    ask_de_multiplied: Decimal
    multiplier: Decimal
    depth_levels: int
    buckets_usd: tuple[Decimal, ...]
    bid_vwap_dm: tuple[Decimal | None, ...]
    ask_vwap_dm: tuple[Decimal | None, ...]
    gap: bool = False


@dataclass(frozen=True, slots=True)
class BybitTradeTick:
    """Public trade print from Bybit, with multiplier already applied."""

    pair_id: str
    symbol: str
    exchange_ts_ms: int
    recv_ts_ms: int
    trade_id: str
    price: Decimal
    price_de_multiplied: Decimal
    size: Decimal
    side: Literal["Buy", "Sell"]
    multiplier: Decimal
    gap: bool = False


@dataclass(frozen=True, slots=True)
class FluxionPoolStateTick:
    """Per-block Fluxion V3 pool snapshot after Multicall3."""

    pair_id: str
    pool: str
    block_number: int
    block_ts: int
    recv_ts_ms: int
    sqrt_price_x96: int
    tick: int
    liquidity: int
    token0: str
    token1: str
    # USDC per 1 wrapper share (human units).
    mid_usdc_per_wrapper: Decimal
    # USDC per 1 native xStock after ERC-4626 convertToAssets.
    mid_usdc_per_native: Decimal
    # Native assets per 1 wrapper share (human units).
    wrapper_assets_per_share: Decimal
    gap: bool = False


@dataclass(frozen=True, slots=True)
class FluxionSwapTick:
    """Decoded UniV3-style Swap on a Fluxion pool (AMM mechanism)."""

    pair_id: str
    pool: str
    block_number: int
    block_ts: int
    recv_ts_ms: int
    tx_hash: str
    log_index: int
    sender: str
    recipient: str
    amount0: int
    amount1: int
    sqrt_price_x96: int
    liquidity: int
    tick: int
    # Human units after decimal adjust; positive = pool received that side.
    amount_token0: Decimal
    amount_token1: Decimal
    # Direction from the pool's perspective of the native-comparable leg.
    # "buy_native" = trader bought native (pool sold wrapper/native).
    direction: Literal["buy_native", "sell_native", "unknown"]
    price_usdc_per_wrapper: Decimal | None
    gas_used: int | None = None
    effective_gas_price: int | None = None
    gap: bool = False


@dataclass(frozen=True, slots=True)
class FluxionRfqQuoteTick:
    """One RFQ EXACT_INPUT poll result (including unavailable / HTTP 204)."""

    pair_id: str
    poll_ts_ms: int
    recv_ts_ms: int
    token_in: str
    token_out: str
    amount_in: str
    amount_out: str | None
    price: Decimal | None
    side: str | None
    request_id: str | None
    http_status: int
    available: bool
    gap: bool = False


# Single source of truth for the collector's RFQ ``side`` vocabulary. Writers use
# ``monitor.fluxion.rfq.RfqLeg`` (``buy_native`` / ``sell_native``); the bare
# ``buy`` / ``sell`` spellings are accepted for vendor payloads and fixtures.
RfqSideLeg = Literal["buy", "sell"]
RFQ_BUY_SIDES: frozenset[str] = frozenset({"buy_native", "buy"})
RFQ_SELL_SIDES: frozenset[str] = frozenset({"sell_native", "sell"})


def rfq_side_leg(side: str | None) -> RfqSideLeg | None:
    """Normalize a stored RFQ ``side`` to its Fluxion leg; None if absent/unknown."""
    normalized = (side or "").strip().lower()
    if normalized in RFQ_BUY_SIDES:
        return "buy"
    if normalized in RFQ_SELL_SIDES:
        return "sell"
    return None


@dataclass(frozen=True, slots=True)
class FluxionRfqFillTick:
    """Limit Order Protocol fill event (RFQ settlement; signature-derived topics).

    WHI-768 enriches maker/taker/pair/amounts from the fill receipt Transfer
    graph. Unenriched rows keep the legacy order_hash + remaining fields only.
    """

    block_number: int
    block_ts: int
    recv_ts_ms: int
    tx_hash: str
    log_index: int
    order_hash: str
    remaining_making_amount: int
    gap: bool = False
    # Receipt enrichment (nullable until live enrich / backfill).
    pair_id: str | None = None
    maker: str | None = None
    taker: str | None = None
    direction: str | None = None  # maker side: buy_native | sell_native
    making_token: str | None = None
    taking_token: str | None = None
    making_amount: str | None = None
    taking_amount: str | None = None
    usdc_amount: str | None = None
    stock_amount: str | None = None
    enriched: bool = False


@dataclass(frozen=True, slots=True)
class Erc20TransferTick:
    """xStock native ERC-20 Transfer (inventory asset; WHI-768)."""

    pair_id: str
    token: str
    block_number: int
    block_ts: int
    recv_ts_ms: int
    tx_hash: str
    log_index: int
    frm: str
    to_addr: str
    amount: Decimal  # human units (18 dec for xStocks)
    amount_raw: int
    gap: bool = False


@dataclass(frozen=True, slots=True)
class UnderlyingPriceTick:
    """Underlying equity reference print (WHI-778).

    Shared by ticker across markets (not pair_id). ``price_type`` must be
    respected by premium UI — never treat ``close`` as live RTH.
    """

    ticker: str
    price: Decimal
    currency: str
    price_type: Literal["live", "pre", "post", "close", "stale"]
    as_of_ms: int
    recv_ts_ms: int
    source: str
    feed_id: str | None = None
    conf: Decimal | None = None
    gap: bool = False


@dataclass(frozen=True, slots=True)
class CollectorGap:
    """Explicit gap window after disconnect / missed blocks / poll stall."""

    source: str
    gap_start_ms: int
    gap_end_ms: int
    detail: str
