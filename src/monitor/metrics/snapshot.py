"""Build edge snapshots from live quote ticks (M3 glue for M5 TUI).

Pure functions over ``monitor.quotes`` shapes — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from monitor.metrics.config import MetricsConfig
from monitor.metrics.edge import (
    EdgeResult,
    VenueKind,
    compute_edge_ladder,
    mid_from_bid_ask,
    spread_bps,
)
from monitor.metrics.session import SessionKind, session_kind
from monitor.quotes import BybitBookTick, FluxionPoolStateTick, FluxionRfqQuoteTick


@dataclass(frozen=True, slots=True)
class SpreadSnapshot:
    """One-moment Bybit mid vs AMM and vs RFQ (bps of Bybit mid)."""

    pair_id: str
    ts_ms: int
    bybit_mid: Decimal
    amm_mid: Decimal | None
    rfq_mid: Decimal | None
    amm_spread_bps: Decimal | None  # (amm - bybit) / bybit * 1e4
    rfq_spread_bps: Decimal | None
    session: SessionKind


@dataclass(frozen=True, slots=True)
class EdgeSnapshot:
    pair_id: str
    ts_ms: int
    session: SessionKind
    spreads: SpreadSnapshot
    amm_edges: list[EdgeResult]
    rfq_edges: list[EdgeResult]


def rfq_mid_from_tick(tick: FluxionRfqQuoteTick) -> Decimal | None:
    """Prefer explicit price; else amount_out/amount_in when both present."""
    if not tick.available:
        return None
    if tick.price is not None and tick.price > 0:
        return tick.price
    if tick.amount_out is None:
        return None
    try:
        ain = Decimal(tick.amount_in)
        aout = Decimal(tick.amount_out)
    except Exception:
        return None
    if ain <= 0 or aout <= 0:
        return None
    # EXACT_INPUT: if token_in is quote (USDC 6 dec) buying native, price ≈
    # amount_in_human / amount_out_human. Without decimals here we only use
    # tick.price; raw ratio is not human mid. Require price field.
    return None


def build_spread_snapshot(
    *,
    bybit: BybitBookTick,
    amm: FluxionPoolStateTick | None,
    rfq: FluxionRfqQuoteTick | None,
    config: MetricsConfig,
    ts_ms: int | None = None,
) -> SpreadSnapshot:
    ts = ts_ms if ts_ms is not None else bybit.recv_ts_ms
    bybit_mid = mid_from_bid_ask(bybit.bid_de_multiplied, bybit.ask_de_multiplied)
    amm_mid = amm.mid_usdc_per_native if amm is not None else None
    rfq_mid = rfq_mid_from_tick(rfq) if rfq is not None else None
    dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
    sk = session_kind(dt, config=config)
    return SpreadSnapshot(
        pair_id=bybit.pair_id,
        ts_ms=ts,
        bybit_mid=bybit_mid,
        amm_mid=amm_mid,
        rfq_mid=rfq_mid,
        amm_spread_bps=spread_bps(bybit_mid, amm_mid) if amm_mid is not None else None,
        rfq_spread_bps=spread_bps(bybit_mid, rfq_mid) if rfq_mid is not None else None,
        session=sk,
    )


def build_edge_snapshot(
    *,
    bybit: BybitBookTick,
    amm: FluxionPoolStateTick | None,
    rfq: FluxionRfqQuoteTick | None,
    config: MetricsConfig,
    pool_fee: int | None = None,
    token0_is_quote: bool = True,
    token0_decimals: int = 6,
    token1_decimals: int = 18,
    ts_ms: int | None = None,
) -> EdgeSnapshot:
    spreads = build_spread_snapshot(
        bybit=bybit, amm=amm, rfq=rfq, config=config, ts_ms=ts_ms
    )
    amm_edges: list[EdgeResult] = []
    if amm is not None and pool_fee is not None:
        amm_edges = compute_edge_ladder(
            pair_id=bybit.pair_id,
            bybit_bid=bybit.bid_de_multiplied,
            bybit_ask=bybit.ask_de_multiplied,
            fluxion_mid=amm.mid_usdc_per_native,
            venue="amm",
            config=config,
            pool_fee=pool_fee,
            sqrt_price_x96=amm.sqrt_price_x96,
            liquidity=amm.liquidity,
            token0_is_quote=token0_is_quote,
            token0_decimals=token0_decimals,
            token1_decimals=token1_decimals,
        )
    rfq_edges: list[EdgeResult] = []
    if spreads.rfq_mid is not None:
        rfq_edges = compute_edge_ladder(
            pair_id=bybit.pair_id,
            bybit_bid=bybit.bid_de_multiplied,
            bybit_ask=bybit.ask_de_multiplied,
            fluxion_mid=spreads.rfq_mid,
            venue="rfq",
            config=config,
        )
    return EdgeSnapshot(
        pair_id=bybit.pair_id,
        ts_ms=spreads.ts_ms,
        session=spreads.session,
        spreads=spreads,
        amm_edges=amm_edges,
        rfq_edges=rfq_edges,
    )


def best_edge_for_venue(
    edges: list[EdgeResult],
    *,
    venue: VenueKind,
    size_usd: Decimal | None = None,
) -> EdgeResult | None:
    candidates = [e for e in edges if e.venue == venue and e.fillable]
    if size_usd is not None:
        candidates = [e for e in candidates if e.size_usd == size_usd]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e.net_edge_bps)
