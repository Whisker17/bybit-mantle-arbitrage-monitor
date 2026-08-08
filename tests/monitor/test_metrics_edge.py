"""Seam: spread_bps + compute_edge — hand-recomputable net space."""

from __future__ import annotations

from decimal import Decimal

from monitor.fluxion.pools import mid_from_sqrt_price_x96
from monitor.metrics import (
    AmmPoolState,
    compute_edge,
    load_metrics_config,
    mid_from_bid_ask,
    spread_bps,
)
from monitor.metrics.amm_slip import fee_bps_from_pool_fee, gas_bps
from monitor.metrics.bybit_slip import half_spread_bps, l1_slip_bps_from_mid
from monitor.metrics.edge import best_net_edge, compute_edge_ladder


def test_mid_and_spread_bps() -> None:
    bid, ask = Decimal("100"), Decimal("100.20")
    mid = mid_from_bid_ask(bid, ask)
    assert mid == Decimal("100.10")
    other = Decimal("101")
    expected = (other - mid) / mid * Decimal(10_000)
    assert spread_bps(mid, other) == expected


def test_half_spread_l1_slip() -> None:
    bid, ask = Decimal("100"), Decimal("100.20")
    hs = half_spread_bps(bid, ask)
    assert hs == (ask - bid) / ((bid + ask) / 2) / 2 * Decimal(10_000)
    assert l1_slip_bps_from_mid(bid, ask) == hs


def test_fee_bps_from_pool_fee() -> None:
    assert fee_bps_from_pool_fee(3000) == Decimal(30)
    assert fee_bps_from_pool_fee(500) == Decimal(5)
    assert fee_bps_from_pool_fee(0) == Decimal(0)


def test_gas_bps_hand_calc() -> None:
    assert gas_bps(Decimal("0.01"), Decimal(1000)) == Decimal("0.1")
    assert gas_bps(Decimal("0.01"), Decimal(5000)) == Decimal("0.02")


def test_rfq_edge_hand_recompute() -> None:
    """Acceptance: net space equals the paper formula by hand."""
    cfg = load_metrics_config()
    bid = Decimal("100")
    ask = Decimal("100.20")
    fluxion = Decimal("99.50")
    size = Decimal(1000)

    edge = compute_edge(
        pair_id="TSLAx",
        bybit_bid=bid,
        bybit_ask=ask,
        fluxion_mid=fluxion,
        size_usd=size,
        direction="buy_fluxion_sell_bybit",
        venue="rfq",
        config=cfg,
    )

    bybit_mid = Decimal("100.10")
    gross = (bybit_mid - fluxion) / bybit_mid * Decimal(10_000)
    bybit_slip = half_spread_bps(bid, ask)
    wear = (
        Decimal(20)
        + Decimal(0)
        + bybit_slip
        + Decimal(0)
        + gas_bps(Decimal("0.01"), size)
        + Decimal(0)
    )
    expected_net = gross - wear

    assert edge.fillable
    assert edge.gross_spread_bps == gross
    assert edge.costs.bybit_taker_bps == Decimal(20)
    assert edge.costs.fluxion_fee_bps == Decimal(0)
    assert edge.costs.bybit_slip_bps == bybit_slip
    assert edge.costs.fluxion_slip_bps == Decimal(0)
    assert edge.costs.gas_bps == gas_bps(Decimal("0.01"), size)
    assert edge.costs.total_wear_bps == wear
    assert edge.net_edge_bps == expected_net
    # Gross ~60 bps; wear ~30 bps (20 taker + ~10 half-spread + 0.1 gas).
    assert Decimal("29") < edge.net_edge_bps < Decimal("31")


def _deep_amm(target_mid: Decimal = Decimal("99.5")) -> tuple[AmmPoolState, Decimal]:
    ratio = Decimal(10) ** 12 / target_mid
    sqrt_price_x96 = int(ratio.sqrt() * Decimal(2**96))
    mid = mid_from_sqrt_price_x96(
        sqrt_price_x96,
        token0_is_quote=True,
        token0_decimals=6,
        token1_decimals=18,
    )
    pool = AmmPoolState(
        pool_fee=3000,
        sqrt_price_x96=sqrt_price_x96,
        liquidity=10**18,
        token0_is_quote=True,
        token0_decimals=6,
        token1_decimals=18,
    )
    return pool, mid


def test_amm_edge_includes_pool_fee_and_positive_slip() -> None:
    cfg = load_metrics_config()
    bid = Decimal("100")
    ask = Decimal("100.20")
    pool, mid = _deep_amm()
    assert abs(mid - Decimal("99.5")) / Decimal("99.5") < Decimal("0.001")

    edge = compute_edge(
        pair_id="AAPLx",
        bybit_bid=bid,
        bybit_ask=ask,
        fluxion_mid=mid,
        size_usd=Decimal(1000),
        direction="buy_fluxion_sell_bybit",
        venue="amm",
        config=cfg,
        amm=pool,
    )

    assert edge.fillable
    assert edge.costs.fluxion_fee_bps == Decimal(30)
    assert edge.costs.bybit_taker_bps == Decimal(20)
    assert edge.costs.fluxion_slip_bps >= 0
    bybit_mid = mid_from_bid_ask(bid, ask)
    gross = (bybit_mid - mid) / bybit_mid * Decimal(10_000)
    wear = edge.costs.total_wear_bps
    assert edge.gross_spread_bps == gross
    assert edge.net_edge_bps == gross - wear
    min_wear = (
        Decimal(30)
        + Decimal(20)
        + gas_bps(Decimal("0.01"), Decimal(1000))
        + half_spread_bps(bid, ask)
    )
    assert wear >= min_wear


def test_ladder_and_best_edge() -> None:
    cfg = load_metrics_config()
    results = compute_edge_ladder(
        pair_id="TSLAx",
        bybit_bid=Decimal("100"),
        bybit_ask=Decimal("100.20"),
        fluxion_mid=Decimal("99.50"),
        venue="rfq",
        config=cfg,
    )
    assert len(results) == 6
    sizes = {r.size_usd for r in results}
    assert sizes == {Decimal(1000), Decimal(5000), Decimal(20000)}
    best = best_net_edge(results)
    assert best is not None
    assert best.direction == "buy_fluxion_sell_bybit"
    at_1k = next(
        r
        for r in results
        if r.size_usd == Decimal(1000) and r.direction == "buy_fluxion_sell_bybit"
    )
    at_20k = next(
        r
        for r in results
        if r.size_usd == Decimal(20000) and r.direction == "buy_fluxion_sell_bybit"
    )
    assert at_20k.costs.gas_bps < at_1k.costs.gas_bps
    assert at_20k.net_edge_bps > at_1k.net_edge_bps


def test_opposite_direction_negative_when_fluxion_cheap() -> None:
    cfg = load_metrics_config()
    edge = compute_edge(
        pair_id="TSLAx",
        bybit_bid=Decimal("100"),
        bybit_ask=Decimal("100.20"),
        fluxion_mid=Decimal("99.50"),
        size_usd=Decimal(1000),
        direction="buy_bybit_sell_fluxion",
        venue="rfq",
        config=cfg,
    )
    assert edge.gross_spread_bps < 0
    assert edge.net_edge_bps < 0
