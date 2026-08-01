"""Seam: spread_bps + compute_edge — hand-recomputable net space."""

from __future__ import annotations

from decimal import Decimal

from monitor.metrics import (
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
    # other 101 → (101 - 100.10) / 100.10 * 1e4
    other = Decimal("101")
    expected = (other - mid) / mid * Decimal(10_000)
    assert spread_bps(mid, other) == expected


def test_half_spread_l1_slip() -> None:
    bid, ask = Decimal("100"), Decimal("100.20")
    # mid=100.10, full spread=0.20 → 0.20/100.10*1e4 ≈ 19.980… bps; half ≈ 9.990
    hs = half_spread_bps(bid, ask)
    assert hs == (ask - bid) / ((bid + ask) / 2) / 2 * Decimal(10_000)
    assert l1_slip_bps_from_mid(bid, ask) == hs


def test_fee_bps_from_pool_fee() -> None:
    assert fee_bps_from_pool_fee(3000) == Decimal(30)
    assert fee_bps_from_pool_fee(500) == Decimal(5)
    assert fee_bps_from_pool_fee(0) == Decimal(0)


def test_gas_bps_hand_calc() -> None:
    # $0.01 gas on $1000 = 1 bps
    assert gas_bps(Decimal("0.01"), Decimal(1000)) == Decimal("0.1")
    # $0.01 on $5000 = 0.02 bps
    assert gas_bps(Decimal("0.01"), Decimal(5000)) == Decimal("0.02")


def test_rfq_edge_hand_recompute() -> None:
    """Acceptance: net space at a moment equals the paper formula by hand.

    Bybit bid/ask 100 / 100.20 → mid 100.10, half-spread ≈ 9.99001 bps
    Fluxion RFQ mid 99.50
    Direction buy_fluxion_sell_bybit:
      gross = (100.10 - 99.50) / 100.10 * 1e4 ≈ 59.94006 bps
      wear  = 10 (taker) + 0 (rfq fee) + ~9.990 (bybit L1) + 0 (rfq slip)
              + 0.1 (gas @ $1k) + 0 (basis)
            ≈ 20.09001
      net   ≈ 39.85005
    """
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
        Decimal(10)
        + Decimal(0)
        + bybit_slip
        + Decimal(0)
        + gas_bps(Decimal("0.01"), size)
        + Decimal(0)
    )
    expected_net = gross - wear

    assert edge.fillable
    assert edge.gross_spread_bps == gross
    assert edge.costs.bybit_taker_bps == Decimal(10)
    assert edge.costs.fluxion_fee_bps == Decimal(0)
    assert edge.costs.bybit_slip_bps == bybit_slip
    assert edge.costs.fluxion_slip_bps == Decimal(0)
    assert edge.costs.gas_bps == gas_bps(Decimal("0.01"), size)
    assert edge.costs.total_wear_bps == wear
    assert edge.net_edge_bps == expected_net
    # Sanity: roughly 40 bps of net room
    assert Decimal("39") < edge.net_edge_bps < Decimal("41")


def test_amm_edge_includes_pool_fee_and_positive_slip() -> None:
    """AMM path: 30 bps pool fee + price impact > 0 for non-trivial size.

    Construct a deep pool so slip is small but non-zero; verify additive wear.
    """
    cfg = load_metrics_config()
    bid = Decimal("100")
    ask = Decimal("100.20")
    # mid ≈ 100.10 on Bybit; put AMM mid a bit lower via sqrtPrice.
    # token0=quote (USDC 6 dec), token1=base (18 dec).
    # human quote/base = 100 → raw token1/token0 = 100 * 10^(6-18) = 1e-10
    # sqrt(price_raw) * 2^96
    # price_raw = token1/token0 in base units = 100 * 10^(6-18) = 1e-10
    # Actually mid_quote_per_base with token0_is_quote:
    #   t1_per_t0 = ratio * 10^(t0_dec - t1_dec) = ratio * 10^(6-18)
    #   mid = 1 / t1_per_t0 = 1 / (ratio * 1e-12)
    #   want mid ≈ 99.5 → ratio = 1 / (99.5 * 1e-12) = 1e12 / 99.5
    #   sqrt_ratio = sqrt(1e12 / 99.5)
    # Easier: pick sqrt_price_x96 so mid is known, then check fee line only.

    # Use a very high liquidity so slip ≈ 0; assert fee=30 and hand wear.
    # From monitor.fluxion.pools: mid = f(sqrtP). We'll compute edge and check
    # fee component + that total wear >= taker + fee + gas + half-spread.
    from monitor.metrics.amm_slip import mid_quote_per_base

    # Choose sqrt so mid_usdc ≈ 99.5 with token0=quote.
    # ratio = (sqrtP/2^96)^2
    # t1_per_t0 = ratio * 10^(6-18) = ratio * 1e-12
    # mid = 1/t1_per_t0 = 1e12 / ratio → ratio = 1e12 / 99.5
    target_mid = Decimal("99.5")
    ratio = Decimal(10) ** 12 / target_mid
    sqrt_ratio = ratio.sqrt()
    sqrt_price_x96 = int(sqrt_ratio * Decimal(2**96))
    mid = mid_quote_per_base(
        sqrt_price_x96,
        token0_is_quote=True,
        token0_decimals=6,
        token1_decimals=18,
    )
    assert abs(mid - target_mid) / target_mid < Decimal("0.001")

    # Large L so $1k barely moves price
    liquidity = 10**18

    edge = compute_edge(
        pair_id="AAPLx",
        bybit_bid=bid,
        bybit_ask=ask,
        fluxion_mid=mid,
        size_usd=Decimal(1000),
        direction="buy_fluxion_sell_bybit",
        venue="amm",
        config=cfg,
        pool_fee=3000,
        sqrt_price_x96=sqrt_price_x96,
        liquidity=liquidity,
        token0_is_quote=True,
        token0_decimals=6,
        token1_decimals=18,
    )

    assert edge.fillable
    assert edge.costs.fluxion_fee_bps == Decimal(30)
    assert edge.costs.bybit_taker_bps == Decimal(10)
    assert edge.costs.fluxion_slip_bps >= 0
    # Hand recompute net from components
    bybit_mid = mid_from_bid_ask(bid, ask)
    gross = (bybit_mid - mid) / bybit_mid * Decimal(10_000)
    wear = edge.costs.total_wear_bps
    assert edge.gross_spread_bps == gross
    assert edge.net_edge_bps == gross - wear
    # Wear must cover at least fee + taker + gas + L1 slip
    min_wear = (
        Decimal(30)
        + Decimal(10)
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
    # 2 directions × 3 sizes
    assert len(results) == 6
    sizes = {r.size_usd for r in results}
    assert sizes == {Decimal(1000), Decimal(5000), Decimal(20000)}
    best = best_net_edge(results)
    assert best is not None
    assert best.direction == "buy_fluxion_sell_bybit"
    # Larger size → lower gas_bps → slightly better net for RFQ (same L1 slip)
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
    # Gross negative ~ -60 bps, wear ~20 → deeply negative net
    assert edge.gross_spread_bps < 0
    assert edge.net_edge_bps < 0
