"""Build edge snapshots from live quote ticks (M3 glue for M5 TUI).

Pure functions over ``monitor.quotes`` shapes — no I/O. The TUI (M5) is the
producer that feeds successive snapshots into ``EdgeStats``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from monitor.metrics.amm_pool import AmmPoolState
from monitor.metrics.amm_quote import (
    AmmQuoteReason,
    amm_quote_for_cex,
    is_tradable_amm_quote,
)
from monitor.metrics.config import MetricsConfig
from monitor.metrics.edge import (
    Direction,
    EdgeResult,
    compute_edge,
    compute_edge_ladder,
    mid_from_bid_ask,
    spread_bps,
)
from monitor.metrics.session import SessionKind, session_kind
from monitor.quotes import (
    BybitBookTick,
    FluxionPoolStateTick,
    FluxionRfqQuoteTick,
    rfq_side_leg,
)


@dataclass(frozen=True, slots=True)
class SpreadSnapshot:
    """One-moment Bybit mid vs AMM and vs RFQ (bps of Bybit mid)."""

    pair_id: str
    ts_ms: int
    bybit_mid: Decimal
    amm_mid: Decimal | None
    rfq_buy_mid: Decimal | None  # USDC→native executable (buy base on Fluxion)
    rfq_sell_mid: Decimal | None  # native→USDC executable (sell base on Fluxion)
    amm_spread_bps: Decimal | None
    rfq_buy_spread_bps: Decimal | None
    rfq_sell_spread_bps: Decimal | None
    session: SessionKind
    # WHI-795: why amm_mid is None when a pool tick was present (empty_pool / …).
    amm_quote_reason: AmmQuoteReason | None = None


@dataclass(frozen=True, slots=True)
class EdgeSnapshot:
    pair_id: str
    ts_ms: int
    session: SessionKind
    spreads: SpreadSnapshot
    amm_edges: list[EdgeResult]
    rfq_edges: list[EdgeResult]


def rfq_price(tick: FluxionRfqQuoteTick | None) -> Decimal | None:
    if tick is None or not tick.available:
        return None
    if tick.price is not None and tick.price > 0:
        return tick.price
    return None


def _rfq_side_matches(tick: FluxionRfqQuoteTick, direction: Direction) -> bool:
    """Map RFQ poll side to paper-arb direction.

    Side vocabulary lives in ``monitor.quotes`` (``rfq_side_leg``). A missing side
    is accepted so tests and vendor payloads without one still produce edges;
    callers already route via the ``rfq_buy`` / ``rfq_sell`` parameters.
    """
    if not (tick.side or "").strip():
        return True
    expected = "buy" if direction == "buy_fluxion_sell_bybit" else "sell"
    return rfq_side_leg(tick.side) == expected


def build_spread_snapshot(
    *,
    bybit: BybitBookTick,
    amm: FluxionPoolStateTick | None,
    config: MetricsConfig,
    rfq_buy: FluxionRfqQuoteTick | None = None,
    rfq_sell: FluxionRfqQuoteTick | None = None,
    ts_ms: int | None = None,
) -> SpreadSnapshot:
    """Build dual spread series: Bybit mid vs AMM, vs RFQ buy, vs RFQ sell.

    AMM mid is suppressed when the pool is not quotable (empty liquidity /
    non-positive residual slot0 mid — WHI-795). Liquid pools with |vs CEX|
    above ``config.max_abs_amm_spread_bps`` keep mid/spread but set reason
    ``pricing_anomaly`` (WHI-822) so tradable consumers refuse the claim.
    RFQ is independent.
    """
    ts = ts_ms if ts_ms is not None else bybit.recv_ts_ms
    bybit_mid = mid_from_bid_ask(bybit.bid_de_multiplied, bybit.ask_de_multiplied)
    amm_mid, amm_reason = amm_quote_for_cex(
        amm,
        cex_mid=bybit_mid,
        max_abs_spread_bps=config.max_abs_amm_spread_bps,
    )
    buy_mid = rfq_price(rfq_buy)
    sell_mid = rfq_price(rfq_sell)
    dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
    sk = session_kind(dt, config=config)
    return SpreadSnapshot(
        pair_id=bybit.pair_id,
        ts_ms=ts,
        bybit_mid=bybit_mid,
        amm_mid=amm_mid,
        rfq_buy_mid=buy_mid,
        rfq_sell_mid=sell_mid,
        amm_spread_bps=spread_bps(bybit_mid, amm_mid) if amm_mid is not None else None,
        rfq_buy_spread_bps=(
            spread_bps(bybit_mid, buy_mid) if buy_mid is not None else None
        ),
        rfq_sell_spread_bps=(
            spread_bps(bybit_mid, sell_mid) if sell_mid is not None else None
        ),
        session=sk,
        amm_quote_reason=amm_reason,
    )


def build_edge_snapshot(
    *,
    bybit: BybitBookTick,
    config: MetricsConfig,
    amm: FluxionPoolStateTick | None = None,
    amm_pool: AmmPoolState | None = None,
    rfq_buy: FluxionRfqQuoteTick | None = None,
    rfq_sell: FluxionRfqQuoteTick | None = None,
    ts_ms: int | None = None,
    asset_withdrawal_fee_tokens: Decimal | None = None,
    price_multiplier: Decimal = Decimal(1),
) -> EdgeSnapshot:
    spreads = build_spread_snapshot(
        bybit=bybit,
        amm=amm,
        config=config,
        rfq_buy=rfq_buy,
        rfq_sell=rfq_sell,
        ts_ms=ts_ms,
    )
    amm_edges: list[EdgeResult] = []
    # Same quotability gate as spreads — empty pool / pricing anomaly must not
    # produce net edge (WHI-795 / WHI-822). Mid may still be present under
    # pricing_anomaly for investigation; tradable claim requires reason is None.
    if (
        amm_pool is not None
        and spreads.amm_mid is not None
        and is_tradable_amm_quote(spreads.amm_quote_reason)
    ):
        amm_edges = compute_edge_ladder(
            pair_id=bybit.pair_id,
            bybit_bid=bybit.bid_de_multiplied,
            bybit_ask=bybit.ask_de_multiplied,
            fluxion_mid=spreads.amm_mid,
            venue="amm",
            config=config,
            amm=amm_pool,
            asset_withdrawal_fee_tokens=asset_withdrawal_fee_tokens,
            price_multiplier=price_multiplier,
        )

    rfq_edges: list[EdgeResult] = []
    # Side-aware: each RFQ quote only feeds its matching direction.
    rfq_legs: list[tuple[FluxionRfqQuoteTick | None, Direction]] = [
        (rfq_buy, "buy_fluxion_sell_bybit"),
        (rfq_sell, "buy_bybit_sell_fluxion"),
    ]
    for tick, direction in rfq_legs:
        price = rfq_price(tick)
        if price is None or tick is None:
            continue
        if not _rfq_side_matches(tick, direction):
            continue
        for size in config.size_ladder_usd:
            rfq_edges.append(
                compute_edge(
                    pair_id=bybit.pair_id,
                    bybit_bid=bybit.bid_de_multiplied,
                    bybit_ask=bybit.ask_de_multiplied,
                    fluxion_mid=price,
                    size_usd=size,
                    direction=direction,
                    venue="rfq",
                    config=config,
                    asset_withdrawal_fee_tokens=asset_withdrawal_fee_tokens,
                    price_multiplier=price_multiplier,
                )
            )

    return EdgeSnapshot(
        pair_id=bybit.pair_id,
        ts_ms=spreads.ts_ms,
        session=spreads.session,
        spreads=spreads,
        amm_edges=amm_edges,
        rfq_edges=rfq_edges,
    )



