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
        Decimal(15)
        + Decimal(0)
        + bybit_slip
        + Decimal(0)
        + gas_bps(Decimal("0.01"), size)
        + Decimal(0)
    )
    expected_net = gross - wear

    assert edge.fillable
    assert edge.gross_spread_bps == gross
    assert edge.costs.bybit_taker_bps == Decimal(15)
    assert edge.costs.fluxion_fee_bps == Decimal(0)
    assert edge.costs.bybit_slip_bps == bybit_slip
    assert edge.costs.fluxion_slip_bps == Decimal(0)
    assert edge.costs.gas_bps == gas_bps(Decimal("0.01"), size)
    assert edge.costs.total_wear_bps == wear
    assert edge.net_edge_bps == expected_net
    # Gross ~60 bps; wear ~25 bps (15 taker + ~10 half-spread + 0.1 gas).
    assert Decimal("34") < edge.net_edge_bps < Decimal("36")


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
    assert edge.costs.bybit_taker_bps == Decimal(15)
    assert edge.costs.fluxion_slip_bps >= 0
    bybit_mid = mid_from_bid_ask(bid, ask)
    gross = (bybit_mid - mid) / bybit_mid * Decimal(10_000)
    wear = edge.costs.total_wear_bps
    assert edge.gross_spread_bps == gross
    assert edge.net_edge_bps == gross - wear
    min_wear = (
        Decimal(30)
        + Decimal(15)
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


def test_basis_wear_signed_by_direction_on_edge() -> None:
    """WHI-960: M3 cost breakdown signs basis by direction (bot parity)."""
    from monitor.metrics.config import MetricsConfig, PnlV2Config, SessionConfig
    from monitor.metrics.edge import basis_wear_bps

    basis = Decimal("7.5")
    assert basis_wear_bps(basis, "buy_fluxion_sell_bybit") == basis
    assert basis_wear_bps(basis, "buy_bybit_sell_fluxion") == -basis

    cfg = MetricsConfig(
        version=1,
        size_ladder_usd=[Decimal(1000), Decimal(5000), Decimal(20000)],
        bybit_taker_fee_bps=Decimal(0),
        usdt_usdc_basis_bps=basis,
        gas_usd_per_swap=Decimal(0),
        session=SessionConfig(
            timezone="America/New_York",
            open="09:30",
            close="16:00",
            early_close="13:00",
        ),
        breach_size_usd=Decimal(1000),
        max_breach_gap_ms=300_000,
        pnl_v2=PnlV2Config.model_validate(
            {
                "buckets_usd": [10, 50, 100, 500, 1000, 10000],
                "q_min_usd": 10,
                "config_cap_usd": 10000,
            }
        ),
    )
    mid = Decimal(100)
    dir1 = compute_edge(
        pair_id="T",
        bybit_bid=mid,
        bybit_ask=mid,
        fluxion_mid=mid,
        size_usd=Decimal(1000),
        direction="buy_fluxion_sell_bybit",
        venue="rfq",
        config=cfg,
    )
    dir2 = compute_edge(
        pair_id="T",
        bybit_bid=mid,
        bybit_ask=mid,
        fluxion_mid=mid,
        size_usd=Decimal(1000),
        direction="buy_bybit_sell_fluxion",
        venue="rfq",
        config=cfg,
    )
    assert dir1.costs.basis_bps == basis
    assert dir2.costs.basis_bps == -basis
    # Zero gross + zero other wear → net = −signed basis.
    assert dir1.net_edge_bps == -basis
    assert dir2.net_edge_bps == basis


def test_withdrawal_fee_on_edge_direction_aware() -> None:
    """WHI-961: M3 wear includes dir-aware withdrawal (bps of Q)."""
    from monitor.metrics.config import MetricsConfig, PnlV2Config, SessionConfig
    from monitor.metrics.edge import withdrawal_fee_bps, withdrawal_fee_usd_for_direction

    mid = Decimal(100)
    fee_tokens = Decimal("0.01")
    listed = mid * Decimal(1)
    fee_usd, kind = withdrawal_fee_usd_for_direction(
        direction="buy_bybit_sell_fluxion",
        stable_fee_usd=Decimal(0),
        asset_fee_tokens=fee_tokens,
        listed_token_price_usd=listed,
    )
    assert kind == "asset"
    assert fee_usd == Decimal(1)
    size = Decimal(1000)
    assert withdrawal_fee_bps(fee_usd, size) == Decimal(10)

    from monitor.metrics.withdrawal import is_unpriced_dir2

    assert is_unpriced_dir2("buy_bybit_sell_fluxion", None) is True
    assert is_unpriced_dir2("buy_bybit_sell_fluxion", Decimal("0.005")) is False
    assert is_unpriced_dir2("buy_fluxion_sell_bybit", None) is False

    cfg = MetricsConfig(
        version=1,
        size_ladder_usd=[Decimal(1000), Decimal(5000), Decimal(20000)],
        bybit_taker_fee_bps=Decimal(0),
        usdt_usdc_basis_bps=Decimal(0),
        stable_withdrawal_fee_usd=Decimal(0),
        gas_usd_per_swap=Decimal(0),
        session=SessionConfig(
            timezone="America/New_York",
            open="09:30",
            close="16:00",
            early_close="13:00",
        ),
        breach_size_usd=Decimal(1000),
        max_breach_gap_ms=300_000,
        pnl_v2=PnlV2Config.model_validate(
            {
                "buckets_usd": [10, 50, 100, 500, 1000, 10000],
                "q_min_usd": 10,
                "config_cap_usd": 10000,
            }
        ),
    )
    dir1 = compute_edge(
        pair_id="HOODx",
        bybit_bid=mid,
        bybit_ask=mid,
        fluxion_mid=mid,
        size_usd=size,
        direction="buy_fluxion_sell_bybit",
        venue="rfq",
        config=cfg,
        asset_withdrawal_fee_tokens=fee_tokens,
        price_multiplier=Decimal(1),
    )
    dir2 = compute_edge(
        pair_id="HOODx",
        bybit_bid=mid,
        bybit_ask=mid,
        fluxion_mid=mid,
        size_usd=size,
        direction="buy_bybit_sell_fluxion",
        venue="rfq",
        config=cfg,
        asset_withdrawal_fee_tokens=fee_tokens,
        price_multiplier=Decimal(1),
    )
    assert dir1.costs.withdrawal_fee_usd == Decimal(0)
    assert dir1.costs.withdrawal_fee_kind == "stable"
    assert dir1.costs.withdrawal_fee_bps == Decimal(0)
    assert dir2.costs.withdrawal_fee_usd == Decimal(1)
    assert dir2.costs.withdrawal_fee_kind == "asset"
    assert dir2.costs.withdrawal_fee_bps == Decimal(10)
    assert dir2.net_edge_bps == -Decimal(10)

    unknown = compute_edge(
        pair_id="TSLAx",
        bybit_bid=mid,
        bybit_ask=mid,
        fluxion_mid=mid,
        size_usd=size,
        direction="buy_bybit_sell_fluxion",
        venue="rfq",
        config=cfg,
        asset_withdrawal_fee_tokens=None,
    )
    assert unknown.costs.withdrawal_fee_kind == "unknown"
    assert unknown.costs.withdrawal_fee_usd == Decimal(0)
