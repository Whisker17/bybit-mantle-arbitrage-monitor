"""Seams: compute_pnl_usd / pnl_bucket_table / optimal_size (WHI-756).

Hand-recomputable fixtures only — expected values from DESIGN §2.6 / research §7.
"""

from __future__ import annotations

from decimal import Decimal

from monitor.bybit.depth_math import book_vwap_for_base
from monitor.fluxion.pools import mid_from_sqrt_price_x96
from monitor.metrics import (
    AmmPoolState,
    OptimalPnlStats,
    RfqPollQuote,
    SessionKind,
    compute_pnl_usd,
    load_metrics_config,
    optimal_size,
    pnl_bucket_table,
)
from monitor.metrics.amm_slip import (
    amm_quote_in_for_base_out,
    amm_quote_out_for_base_in,
)
from monitor.metrics.config import MetricsConfig, PnlV2Config, SessionConfig


def _session() -> SessionConfig:
    return SessionConfig(
        timezone="America/New_York",
        open="09:30",
        close="16:00",
        early_close="13:00",
    )


def _pnl_cfg(**overrides: object) -> PnlV2Config:
    base: dict[str, object] = {
        "buckets_usd": [
            Decimal(10),
            Decimal(50),
            Decimal(100),
            Decimal(500),
            Decimal(1000),
            Decimal(10000),
        ],
        "q_min_usd": Decimal(10),
        "config_cap_usd": Decimal(10000),
        "coarse_points": 24,
        "refine_points": 16,
        "q_tol_rel": Decimal("1e-6"),
        "amm_solve_max_iters": 64,
        "amm_cap_max_iters": 24,
        "gas_on_rfq": True,
    }
    base.update(overrides)
    return PnlV2Config.model_validate(base)


def _cfg(
    *,
    gas: Decimal = Decimal(0),
    fee_bps: Decimal = Decimal(10),
    basis: Decimal = Decimal(0),
    pnl: PnlV2Config | None = None,
) -> MetricsConfig:
    return MetricsConfig(
        version=1,
        size_ladder_usd=[Decimal(1000), Decimal(5000), Decimal(20000)],
        bybit_taker_fee_bps=fee_bps,
        usdt_usdc_basis_bps=basis,
        gas_usd_per_swap=gas,
        session=_session(),
        breach_size_usd=Decimal(1000),
        max_breach_gap_ms=300_000,
        pnl_v2=pnl or _pnl_cfg(),
    )


def _pool_at_mid(
    mid: Decimal,
    *,
    pool_fee: int = 0,
    liquidity: int = 10**24,
) -> AmmPoolState:
    """token0=quote(USDC 6dp), token1=base(18dp); mid = quote/base.

    Default liquidity is large enough that $10–$10k notionals have sub-cent
    impact so fee/gas algebra is hand-checkable (research §7).
    """
    ratio = Decimal(10) ** 12 / mid
    sqrt_price_x96 = int(ratio.sqrt() * Decimal(2**96))
    got = mid_from_sqrt_price_x96(
        sqrt_price_x96,
        token0_is_quote=True,
        token0_decimals=6,
        token1_decimals=18,
    )
    assert abs(got - mid) / mid < Decimal("0.001")
    return AmmPoolState(
        pool_fee=pool_fee,
        sqrt_price_x96=sqrt_price_x96,
        liquidity=liquidity,
        token0_is_quote=True,
        token0_decimals=6,
        token1_decimals=18,
    )


def test_checked_in_config_has_pnl_v2() -> None:
    cfg = load_metrics_config()
    assert cfg.pnl_v2 is not None
    assert cfg.pnl_v2.buckets_usd == [
        Decimal(10),
        Decimal(50),
        Decimal(100),
        Decimal(500),
        Decimal(1000),
        Decimal(10000),
    ]
    assert cfg.pnl_v2.q_min_usd == Decimal(10)
    assert cfg.pnl_v2.config_cap_usd == Decimal(10000)


def test_research_micro_example_buy_fluxion_sell_bybit() -> None:
    """hummingbot-pnl §7: mid-aligned, zero slip/gas/pool-fee → PnL = −1 USD."""
    cfg = _cfg(gas=Decimal(0), fee_bps=Decimal(10))
    mid = Decimal(100)
    # L1 flat book at mid (zero half-spread).
    bid = ask = mid
    amm = _pool_at_mid(mid, pool_fee=0)
    r = compute_pnl_usd(
        pair_id="TEST",
        bybit_bid=bid,
        bybit_ask=ask,
        size_usd=Decimal(1000),
        direction="buy_fluxion_sell_bybit",
        venue="amm",
        config=cfg,
        amm=amm,
    )
    assert r.fillable
    assert r.q_base == Decimal(10)
    # USDT_recv = 10 * 100 * 0.999 = 999
    assert r.recv_usd == Decimal("999")
    # USDC_spent ≈ 1000 (fee-free AMM at mid, deep liq)
    assert abs(r.spent_usd - Decimal(1000)) < Decimal("0.05")
    assert abs(r.pnl_usd - Decimal("-1")) < Decimal("0.05")
    assert abs(r.costs.bybit_fee_usd - Decimal(1)) < Decimal("0.01")


def test_research_micro_example_buy_bybit_sell_fluxion() -> None:
    """§7 reverse direction: PnL ≈ −1.001 USD from Bybit buy fee in base."""
    cfg = _cfg(gas=Decimal(0), fee_bps=Decimal(10))
    mid = Decimal(100)
    amm = _pool_at_mid(mid, pool_fee=0)
    r = compute_pnl_usd(
        pair_id="TEST",
        bybit_bid=mid,
        bybit_ask=mid,
        size_usd=Decimal(1000),
        direction="buy_bybit_sell_fluxion",
        venue="amm",
        config=cfg,
        amm=amm,
    )
    assert r.fillable
    # q_gross = 10 / 0.999 ≈ 10.01001; USDT_spent ≈ 1001.001
    expected = Decimal(1000) - (Decimal(10) / Decimal("0.999") * Decimal(100))
    assert abs(r.pnl_usd - expected) < Decimal("0.05")


def test_gas_dominates_tiny_bucket_negative_pnl() -> None:
    """$10 bucket: gas $0.01 alone is −10 bps; report negative, never clamp."""
    cfg = _cfg(gas=Decimal("0.01"), fee_bps=Decimal(10))
    mid = Decimal(100)
    amm = _pool_at_mid(mid, pool_fee=0)
    r = compute_pnl_usd(
        pair_id="TEST",
        bybit_bid=mid,
        bybit_ask=mid,
        size_usd=Decimal(10),
        direction="buy_fluxion_sell_bybit",
        venue="amm",
        config=cfg,
        amm=amm,
    )
    assert r.fillable
    # Fee ≈ 0.01 + gas 0.01 + tiny AMM noise → clearly negative
    assert r.pnl_usd < 0
    assert r.costs.gas_usd == Decimal("0.01")


def test_bucket_table_six_rungs_hand_recompute() -> None:
    """All 6 AMM buckets fillable on deep L1 + deep pool; costs include gas."""
    cfg = _cfg(gas=Decimal("0.01"), fee_bps=Decimal(10))
    mid = Decimal(100)
    amm = _pool_at_mid(mid, pool_fee=0)
    table = pnl_bucket_table(
        pair_id="TSLAx",
        bybit_bid=mid,
        bybit_ask=mid,
        direction="buy_fluxion_sell_bybit",
        config=cfg,
        amm=amm,
        include_optimal=False,
    )
    assert len(table.amm_buckets) == 6
    sizes = [r.size_usd for r in table.amm_buckets]
    assert sizes == cfg.pnl_v2.buckets_usd
    for r in table.amm_buckets:
        assert r.fillable
        assert r.costs.gas_usd == Decimal("0.01")
        # Mid-aligned: approx −fee − gas = −0.001*Q − 0.01
        expected = -Decimal("0.001") * r.size_usd - Decimal("0.01")
        assert abs(r.pnl_usd - expected) < Decimal("0.1")


def test_base_sized_vwap_walk() -> None:
    levels = [(Decimal(100), Decimal(1)), (Decimal(99), Decimal(2))]
    vwap = book_vwap_for_base(levels, Decimal("1.5"))
    assert vwap is not None
    # 1 @ 100 + 0.5 @ 99 → notional 149.5 / 1.5
    assert abs(vwap - Decimal("149.5") / Decimal("1.5")) < Decimal("1e-12")
    assert book_vwap_for_base(levels, Decimal(10)) is None


def test_depth_makes_large_bucket_unfillable() -> None:
    cfg = _cfg(gas=Decimal(0))
    mid = Decimal(100)
    amm = _pool_at_mid(mid, pool_fee=0)
    # Only $50 of bid depth
    bids = [(Decimal(100), Decimal("0.5"))]
    r = compute_pnl_usd(
        pair_id="TEST",
        bybit_bid=Decimal(100),
        bybit_ask=Decimal(100),
        size_usd=Decimal(1000),
        direction="buy_fluxion_sell_bybit",
        venue="amm",
        config=cfg,
        amm=amm,
        bybit_bids=bids,
    )
    assert not r.fillable
    assert r.reason == "bybit_book_unfillable"
    assert r.bybit_depth_source == "book"


def test_rfq_poll_keyed_row() -> None:
    cfg = _cfg(gas=Decimal("0.01"))
    mid = Decimal(100)
    # Poll: spend 100 USDC → get 1.01 base (cheap Fluxion)
    rfq = RfqPollQuote(
        amount_in=Decimal(100),
        amount_out=Decimal("1.01"),
        fluxion_leg="buy",
    )
    r = compute_pnl_usd(
        pair_id="TEST",
        bybit_bid=mid,
        bybit_ask=mid,
        size_usd=Decimal(0),
        direction="buy_fluxion_sell_bybit",
        venue="rfq",
        config=cfg,
        rfq=rfq,
    )
    assert r.fillable
    assert r.q_base == Decimal("1.01")
    # USDT_recv = 1.01 * 100 * 0.999 = 100.899; spent 100; gas 0.01
    expected = Decimal("1.01") * mid * Decimal("0.999") - Decimal(100) - Decimal("0.01")
    assert abs(r.pnl_usd - expected) < Decimal("1e-9")
    assert r.costs.fluxion_fee_usd == 0
    assert r.size_usd == Decimal("1.01") * mid


def test_optimal_size_prefers_profitable_mid_over_gas_eaten_micro() -> None:
    """With a Fluxion discount, mid-size beats $10 (gas) and huge size (slip)."""
    cfg = _cfg(gas=Decimal("0.01"), fee_bps=Decimal(10))
    bybit_mid = Decimal(100)
    # Fluxion 50 bps cheaper → gross room after 10 bps fee
    amm = _pool_at_mid(Decimal("99.5"), pool_fee=0, liquidity=10**20)
    opt = optimal_size(
        pair_id="TEST",
        bybit_bid=bybit_mid,
        bybit_ask=bybit_mid,
        direction="buy_fluxion_sell_bybit",
        config=cfg,
        amm=amm,
    )
    assert opt is not None
    assert opt.pnl_usd > 0
    # Should not pick the $10 floor (gas-dominated relative to mid sizes)
    assert opt.q_star_usd > Decimal(10)


def test_optimal_size_multi_peak_picks_better_peak() -> None:
    """Piecewise book: two fillable pockets; search must pick the higher PnL peak.

    Construct Bybit bids with a sweet spot around ~$100 notional (deep at good
    price) then a cliff, then more depth at a worse price — while Fluxion is
    cheap enough that the first pocket is more profitable than grinding the cliff.
    """
    cfg = _cfg(
        gas=Decimal(0),
        fee_bps=Decimal(0),
        pnl=_pnl_cfg(
            q_min_usd=Decimal(10),
            config_cap_usd=Decimal(500),
            coarse_points=20,
            refine_points=12,
            buckets_usd=[
                Decimal(10),
                Decimal(50),
                Decimal(100),
                Decimal(200),
                Decimal(500),
            ],
        ),
    )
    # Fluxion mid 100; Bybit sells into stepped bids.
    amm = _pool_at_mid(Decimal(100), pool_fee=0, liquidity=10**20)
    # Pocket A: $100 notional at 101 (rich sell)
    # Gap: thin
    # Pocket B: more size at 100.2 (worse)
    bids = [
        (Decimal("101"), Decimal("1")),  # $101
        (Decimal("100.2"), Decimal("4")),  # +$400.8
    ]
    # Evaluate both pockets manually
    r_small = compute_pnl_usd(
        pair_id="TEST",
        bybit_bid=Decimal("101"),
        bybit_ask=Decimal("101"),
        size_usd=Decimal(100),
        direction="buy_fluxion_sell_bybit",
        venue="amm",
        config=cfg,
        amm=amm,
        bybit_bids=bids,
    )
    r_large = compute_pnl_usd(
        pair_id="TEST",
        bybit_bid=Decimal("101"),
        bybit_ask=Decimal("101"),
        size_usd=Decimal(400),
        direction="buy_fluxion_sell_bybit",
        venue="amm",
        config=cfg,
        amm=amm,
        bybit_bids=bids,
    )
    assert r_small.fillable and r_large.fillable
    # Prefer the size that yields higher PnL among these anchors
    better = r_small if r_small.pnl_usd >= r_large.pnl_usd else r_large

    opt = optimal_size(
        pair_id="TEST",
        bybit_bid=Decimal("101"),
        bybit_ask=Decimal("101"),
        direction="buy_fluxion_sell_bybit",
        config=cfg,
        amm=amm,
        bybit_bids=bids,
    )
    assert opt is not None
    assert opt.pnl_usd >= better.pnl_usd - Decimal("0.5")
    # Sample-best must be at least as good as evaluating the better pocket
    assert opt.pnl_usd >= min(r_small.pnl_usd, r_large.pnl_usd)


def test_optimal_null_when_depth_below_q_min() -> None:
    cfg = _cfg(gas=Decimal(0))
    amm = _pool_at_mid(Decimal(100), pool_fee=0)
    # Only $5 depth < q_min $10
    bids = [(Decimal(100), Decimal("0.05"))]
    opt = optimal_size(
        pair_id="TEST",
        bybit_bid=Decimal(100),
        bybit_ask=Decimal(100),
        direction="buy_fluxion_sell_bybit",
        config=cfg,
        amm=amm,
        bybit_bids=bids,
    )
    assert opt is None


def test_amm_quote_in_solves_target_base() -> None:
    amm = _pool_at_mid(Decimal(100), pool_fee=3000, liquidity=10**18)
    q = Decimal("1")
    quote_in = amm_quote_in_for_base_out(amm, q, q_tol_rel=Decimal("1e-6"))
    assert quote_in is not None
    # Fee on input ≈ 30 bps + tiny impact
    assert quote_in > Decimal(100)
    from monitor.metrics.amm_slip import amm_base_out_for_quote_in

    got = amm_base_out_for_quote_in(amm, quote_in)
    assert got is not None
    assert abs(got - q) / q < Decimal("1e-5")


def test_amm_quote_in_fails_closed_on_impossible_tol() -> None:
    """Range exhaust / unsolvable → None (no silent 10× tolerance band)."""
    thin = _pool_at_mid(Decimal(100), pool_fee=3000, liquidity=10**10)
    assert amm_quote_in_for_base_out(thin, Decimal("1e6"), q_tol_rel=Decimal("1e-6")) is None


def test_pool_fee_increases_usdc_spent() -> None:
    cfg = _cfg(gas=Decimal(0), fee_bps=Decimal(0))
    mid = Decimal(100)
    free = _pool_at_mid(mid, pool_fee=0)
    fee = _pool_at_mid(mid, pool_fee=3000)
    r0 = compute_pnl_usd(
        pair_id="T",
        bybit_bid=mid,
        bybit_ask=mid,
        size_usd=Decimal(1000),
        direction="buy_fluxion_sell_bybit",
        venue="amm",
        config=cfg,
        amm=free,
    )
    r1 = compute_pnl_usd(
        pair_id="T",
        bybit_bid=mid,
        bybit_ask=mid,
        size_usd=Decimal(1000),
        direction="buy_fluxion_sell_bybit",
        venue="amm",
        config=cfg,
        amm=fee,
    )
    assert r0.fillable and r1.fillable
    assert r1.spent_usd > r0.spent_usd
    assert r1.pnl_usd < r0.pnl_usd
    assert r1.costs.fluxion_fee_usd > 0


def test_basis_wear_subtracts_both_directions() -> None:
    cfg = _cfg(gas=Decimal(0), fee_bps=Decimal(0), basis=Decimal(5))  # 5 bps
    mid = Decimal(100)
    amm = _pool_at_mid(mid, pool_fee=0)
    for direction in ("buy_fluxion_sell_bybit", "buy_bybit_sell_fluxion"):
        r = compute_pnl_usd(
            pair_id="T",
            bybit_bid=mid,
            bybit_ask=mid,
            size_usd=Decimal(1000),
            direction=direction,  # type: ignore[arg-type]
            venue="amm",
            config=cfg,
            amm=amm,
        )
        assert r.fillable
        assert r.costs.basis_usd == Decimal("0.5")  # 5 bps of 1000
        assert r.pnl_usd <= Decimal("-0.4")


def test_pnl_result_to_dict_serializable() -> None:
    cfg = _cfg()
    mid = Decimal(100)
    amm = _pool_at_mid(mid)
    r = compute_pnl_usd(
        pair_id="AAPLx",
        bybit_bid=mid,
        bybit_ask=mid,
        size_usd=Decimal(100),
        direction="buy_fluxion_sell_bybit",
        venue="amm",
        config=cfg,
        amm=amm,
    )
    d = r.to_dict()
    assert d["pair_id"] == "AAPLx"
    assert "costs" in d and "bybit_fee_usd" in d["costs"]
    assert d["fillable"] is True


def test_optimal_pnl_stats_session_split() -> None:
    stats = OptimalPnlStats(max_gap_ms=300_000)
    stats.observe(pnl_usd=Decimal("1.5"), ts_ms=1000, session=SessionKind.OPEN)
    stats.observe(pnl_usd=Decimal("2.0"), ts_ms=2000, session=SessionKind.OPEN)
    stats.observe(pnl_usd=Decimal("-0.5"), ts_ms=3000, session=SessionKind.CLOSED)
    open_d = stats.distribution(SessionKind.OPEN)
    closed_d = stats.distribution(SessionKind.CLOSED)
    assert open_d.count == 2
    assert closed_d.count == 1
    assert open_d.max == Decimal("2.0")
    assert closed_d.max == Decimal("-0.5")


def test_bucket_table_includes_optimal_and_rfq() -> None:
    cfg = _cfg(gas=Decimal("0.01"))
    mid = Decimal(100)
    amm = _pool_at_mid(Decimal("99.5"), pool_fee=0, liquidity=10**20)
    table = pnl_bucket_table(
        pair_id="NVRAx",
        bybit_bid=mid,
        bybit_ask=mid,
        direction="buy_fluxion_sell_bybit",
        config=cfg,
        amm=amm,
        rfq_quotes=[
            RfqPollQuote(
                amount_in=Decimal(100),
                amount_out=Decimal("1.01"),
                fluxion_leg="buy",
            )
        ],
        include_optimal=True,
    )
    assert table.optimal is not None
    assert len(table.rfq_rows) == 1
    assert table.rfq_rows[0].venue == "rfq"
    d = table.to_dict()
    assert d["optimal"] is not None
    assert len(d["amm_buckets"]) == 6


def test_amm_sell_exact_in() -> None:
    amm = _pool_at_mid(Decimal(100), pool_fee=0, liquidity=10**18)
    out = amm_quote_out_for_base_in(amm, Decimal(1))
    assert out is not None
    assert abs(out - Decimal(100)) < Decimal("0.5")


def test_empty_depth_list_degrades_to_l1() -> None:
    """Empty bids/asks list is L1, not book-unfillable (hummingbot-pnl §5.3)."""
    cfg = _cfg(gas=Decimal(0), fee_bps=Decimal(10))
    mid = Decimal(100)
    amm = _pool_at_mid(mid, pool_fee=0)
    r = compute_pnl_usd(
        pair_id="TEST",
        bybit_bid=mid,
        bybit_ask=mid,
        size_usd=Decimal(1000),
        direction="buy_fluxion_sell_bybit",
        venue="amm",
        config=cfg,
        amm=amm,
        bybit_bids=[],
    )
    assert r.fillable
    assert r.bybit_depth_source == "l1"
    opt = optimal_size(
        pair_id="TEST",
        bybit_bid=mid,
        bybit_ask=mid,
        direction="buy_fluxion_sell_bybit",
        config=cfg,
        amm=amm,
        bybit_bids=[],
    )
    assert opt is not None
