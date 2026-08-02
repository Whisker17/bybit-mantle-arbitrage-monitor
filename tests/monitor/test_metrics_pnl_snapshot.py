"""Seams: build_pnl_pair_snapshot / levels_from_depth_curve / RFQ conversion (WHI-766)."""

from __future__ import annotations

from decimal import Decimal

from monitor.metrics import (
    build_pnl_pair_snapshot,
    levels_from_depth_curve,
    load_metrics_config,
    overview_pnl_summary,
    pnl_bucket_table,
    rfq_tick_to_poll_quote,
)
from monitor.metrics.amm_pool import AmmPoolState
from monitor.metrics.config import MetricsConfig, PnlV2Config, SessionConfig
from monitor.quotes import (
    BybitBookTick,
    BybitDepthTick,
    FluxionPoolStateTick,
    FluxionRfqQuoteTick,
)
from monitor.symbols.models import Pair
from monitor.tui.pool import amm_pool_from_tick


def _session() -> SessionConfig:
    return SessionConfig(
        timezone="America/New_York",
        open="09:30",
        close="16:00",
        early_close="13:00",
    )


def _pnl_cfg() -> PnlV2Config:
    return PnlV2Config.model_validate(
        {
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
    )


def _cfg(
    *,
    gas: Decimal = Decimal(0),
    fee_bps: Decimal = Decimal(10),
) -> MetricsConfig:
    return MetricsConfig(
        version=1,
        size_ladder_usd=[Decimal(1000), Decimal(5000), Decimal(20000)],
        bybit_taker_fee_bps=fee_bps,
        usdt_usdc_basis_bps=Decimal(0),
        gas_usd_per_swap=gas,
        session=_session(),
        breach_size_usd=Decimal(1000),
        max_breach_gap_ms=300_000,
        pnl_v2=_pnl_cfg(),
    )


def _book(
    pair_id: str = "AAPLx",
    *,
    bid: Decimal = Decimal(100),
    ask: Decimal = Decimal(100),
    ts: int = 1_700_000_000_000,
) -> BybitBookTick:
    return BybitBookTick(
        pair_id=pair_id,
        symbol="AAPLXUSDT",
        exchange_ts_ms=ts,
        recv_ts_ms=ts,
        bid=bid,
        ask=ask,
        bid_de_multiplied=bid,
        ask_de_multiplied=ask,
        multiplier=Decimal(1),
    )


def _depth(
    pair_id: str = "AAPLx",
    *,
    buckets: tuple[Decimal, ...] | None = None,
    bid_vwap: tuple[Decimal | None, ...] | None = None,
    ask_vwap: tuple[Decimal | None, ...] | None = None,
    ts: int = 1_700_000_000_000,
) -> BybitDepthTick:
    b = buckets or (
        Decimal(10),
        Decimal(50),
        Decimal(100),
        Decimal(500),
        Decimal(1000),
        Decimal(10000),
    )
    # Flat book at 100 → VWAP = 100 for all fillable rungs.
    flat = tuple(Decimal(100) for _ in b)
    return BybitDepthTick(
        pair_id=pair_id,
        symbol="AAPLXUSDT",
        exchange_ts_ms=ts,
        recv_ts_ms=ts,
        bid=Decimal(100),
        ask=Decimal(100),
        bid_de_multiplied=Decimal(100),
        ask_de_multiplied=Decimal(100),
        multiplier=Decimal(1),
        depth_levels=10,
        buckets_usd=b,
        bid_vwap_dm=bid_vwap if bid_vwap is not None else flat,
        ask_vwap_dm=ask_vwap if ask_vwap is not None else flat,
    )


def _pool_tick(
    pair_id: str = "AAPLx",
    *,
    mid: Decimal = Decimal("99.5"),
    ts: int = 1_700_000_000_000,
) -> FluxionPoolStateTick:
    # Match amm_pool_from_tick defaults: token0=USDC, token1=wrapper.
    ratio = Decimal(10) ** 12 / mid
    sqrt_price_x96 = int(ratio.sqrt() * Decimal(2**96))
    return FluxionPoolStateTick(
        pair_id=pair_id,
        pool="0x2cc6a607f3445d826b9e29f507b3a2e3b9dae106",
        block_number=1,
        block_ts=ts // 1000,
        recv_ts_ms=ts,
        sqrt_price_x96=sqrt_price_x96,
        tick=0,
        liquidity=10**20,
        token0="0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9",
        token1="0x5aa7649fdbda47de64a07ac81d64b682af9c0724",
        mid_usdc_per_wrapper=mid,
        mid_usdc_per_native=mid,
        wrapper_assets_per_share=Decimal(1),
    )


def _load_aapl_pair() -> Pair:
    from monitor.symbols import load_pairs_config

    return load_pairs_config().pair_by_id("AAPLx")


def test_levels_from_flat_vwap_curve_matches_notional() -> None:
    from monitor.bybit.depth_math import book_vwap_for_notional

    depth = _depth()
    bids = levels_from_depth_curve(depth, side="bid")
    assert len(bids) == 6
    # Flat V=100 → each level price 100; total notional = 10000
    total = sum(p * s for p, s in bids)
    assert abs(total - Decimal(10000)) < Decimal("1e-6")
    for p, _s in bids:
        assert abs(p - Decimal(100)) < Decimal("1e-9")
    for q, v in zip(depth.buckets_usd, depth.bid_vwap_dm, strict=True):
        assert v is not None
        got = book_vwap_for_notional(bids, q)
        assert got is not None
        assert abs(got - v) < Decimal("1e-9")


def test_levels_sloped_curve_round_trips_notional_vwap() -> None:
    """Sloped VWAPs must reconstruct without understating slip (WHI-766 review)."""
    from monitor.bybit.depth_math import book_vwap_for_notional

    buckets = (Decimal(100), Decimal(200), Decimal(500))
    # Rising ask VWAP: 10 → 11 → 12
    ask_vwap = (Decimal(10), Decimal(11), Decimal(12))
    depth = _depth(
        buckets=buckets,
        bid_vwap=ask_vwap,  # unused for this side
        ask_vwap=ask_vwap,
    )
    asks = levels_from_depth_curve(depth, side="ask")
    assert len(asks) == 3
    for q, v in zip(buckets, ask_vwap, strict=True):
        got = book_vwap_for_notional(asks, q)
        assert got is not None, f"unfillable at Q={q}"
        assert abs(got - v) < Decimal("1e-9"), f"Q={q}: got {got} want {v}"


def test_levels_stop_at_unfillable_rung() -> None:
    depth = _depth(
        bid_vwap=(
            Decimal(100),
            Decimal(100),
            None,
            None,
            None,
            None,
        ),
        ask_vwap=(
            Decimal(100),
            Decimal(100),
            None,
            None,
            None,
            None,
        ),
    )
    bids = levels_from_depth_curve(depth, side="bid")
    assert len(bids) == 2
    total = sum(p * s for p, s in bids)
    assert abs(total - Decimal(50)) < Decimal("1e-6")


def test_rfq_tick_raw_to_human_buy() -> None:
    # 100 USDC (6dp) → 1.01 base (18dp)
    tick = FluxionRfqQuoteTick(
        pair_id="AAPLx",
        poll_ts_ms=1,
        recv_ts_ms=1,
        token_in="0xusdc",
        token_out="0xbase",
        amount_in="100000000",
        amount_out="1010000000000000000",
        price=Decimal("99.0099"),
        side="buy_native",
        request_id="r1",
        http_status=200,
        available=True,
    )
    q = rfq_tick_to_poll_quote(tick, native_decimals=18)
    assert q is not None
    assert q.fluxion_leg == "buy"
    assert q.amount_in == Decimal(100)
    assert q.amount_out == Decimal("1.01")


def test_snapshot_no_book() -> None:
    pair = _load_aapl_pair()
    pool_tick = _pool_tick()
    amm = amm_pool_from_tick(pair, pool_tick)
    snap = build_pnl_pair_snapshot(
        pair_id=pair.id,
        bybit=None,
        amm=amm,
        amm_tick=pool_tick,
        config=_cfg(),
        depth=_depth(),
        native_decimals=pair.fluxion.native_decimals,
    )
    assert snap.status == "no_book"
    assert snap.best.status == "no_book"
    assert snap.tables == {}


def test_snapshot_empty_pool_not_no_fillable() -> None:
    """WHI-795: liquidity==0 residual mid → empty_pool (aligned with spreads)."""
    pair = _load_aapl_pair()
    pool_tick = _pool_tick(mid=Decimal("66.89"))
    # Rebuild with zero liquidity (residual mid still set).
    pool_tick = FluxionPoolStateTick(
        pair_id=pool_tick.pair_id,
        pool=pool_tick.pool,
        block_number=pool_tick.block_number,
        block_ts=pool_tick.block_ts,
        recv_ts_ms=pool_tick.recv_ts_ms,
        sqrt_price_x96=pool_tick.sqrt_price_x96,
        tick=pool_tick.tick,
        liquidity=0,
        token0=pool_tick.token0,
        token1=pool_tick.token1,
        mid_usdc_per_wrapper=pool_tick.mid_usdc_per_wrapper,
        mid_usdc_per_native=pool_tick.mid_usdc_per_native,
        wrapper_assets_per_share=pool_tick.wrapper_assets_per_share,
    )
    amm = amm_pool_from_tick(pair, pool_tick)
    assert amm is not None
    snap = build_pnl_pair_snapshot(
        pair_id=pair.id,
        bybit=_book(),
        amm=amm,
        amm_tick=pool_tick,
        config=_cfg(),
        depth=_depth(),
        native_decimals=pair.fluxion.native_decimals,
    )
    assert snap.status == "empty_pool"
    assert snap.best.status == "empty_pool"
    # Tables still materialize (AMM unfillable); RFQ can still attach when present.
    assert snap.tables != {}


def test_snapshot_invalid_mid_status_matches_quote_reason() -> None:
    """WHI-795: liquidity>0 but mid<=0 must not mask as empty_pool in PnL."""
    pair = _load_aapl_pair()
    pool_tick = _pool_tick(mid=Decimal("99.5"))
    pool_tick = FluxionPoolStateTick(
        pair_id=pool_tick.pair_id,
        pool=pool_tick.pool,
        block_number=pool_tick.block_number,
        block_ts=pool_tick.block_ts,
        recv_ts_ms=pool_tick.recv_ts_ms,
        sqrt_price_x96=pool_tick.sqrt_price_x96,
        tick=pool_tick.tick,
        liquidity=10**20,
        token0=pool_tick.token0,
        token1=pool_tick.token1,
        mid_usdc_per_wrapper=Decimal(0),
        mid_usdc_per_native=Decimal(0),
        wrapper_assets_per_share=Decimal(1),
    )
    amm = amm_pool_from_tick(pair, pool_tick)
    assert amm is not None
    snap = build_pnl_pair_snapshot(
        pair_id=pair.id,
        bybit=_book(),
        amm=amm,
        amm_tick=pool_tick,
        config=_cfg(),
        depth=_depth(),
        native_decimals=pair.fluxion.native_decimals,
    )
    assert snap.status == "invalid_mid"
    assert snap.best.status == "invalid_mid"
    assert snap.tables != {}


def test_snapshot_no_depth_hides_overview_optimal() -> None:
    """Overview must show no_depth; detail tables still compute on L1."""
    pair = _load_aapl_pair()
    cfg = _cfg(gas=Decimal("0.01"), fee_bps=Decimal(10))
    pool_tick = _pool_tick(mid=Decimal("99.5"))
    amm = amm_pool_from_tick(pair, pool_tick)
    snap = build_pnl_pair_snapshot(
        pair_id=pair.id,
        bybit=_book(),
        amm=amm,
        amm_tick=pool_tick,
        config=cfg,
        depth=None,
        native_decimals=pair.fluxion.native_decimals,
    )
    assert snap.status == "no_depth"
    assert snap.has_depth is False
    assert len(snap.tables) == 2
    ov = overview_pnl_summary(snap)
    assert ov.status == "no_depth"
    assert ov.optimal_net_pnl_usd is None
    # Detail tables still have 6 buckets each.
    for table in snap.tables.values():
        assert len(table.amm_buckets) == 6


def test_snapshot_with_depth_matches_engine_bucket_pnl() -> None:
    """Fixed journal-like ticks → bucket PnL equals pure pnl_bucket_table."""
    pair = _load_aapl_pair()
    cfg = _cfg(gas=Decimal("0.01"), fee_bps=Decimal(10))
    mid = Decimal(100)
    flux = Decimal("99.5")
    book = _book(bid=mid, ask=mid)
    pool_tick = _pool_tick(mid=flux)
    depth = _depth()
    amm = amm_pool_from_tick(pair, pool_tick)
    assert amm is not None
    snap = build_pnl_pair_snapshot(
        pair_id=pair.id,
        bybit=book,
        amm=amm,
        amm_tick=pool_tick,
        config=cfg,
        depth=depth,
        native_decimals=pair.fluxion.native_decimals,
    )
    assert snap.status == "ok"
    assert snap.has_depth is True
    assert snap.best.optimal_net_pnl_usd is not None

    bids = levels_from_depth_curve(depth, side="bid")
    asks = levels_from_depth_curve(depth, side="ask")
    expected = pnl_bucket_table(
        pair_id=pair.id,
        bybit_bid=mid,
        bybit_ask=mid,
        direction="buy_fluxion_sell_bybit",
        config=cfg,
        amm=amm,
        bybit_bids=bids,
        bybit_asks=asks,
        include_optimal=True,
    )
    got = snap.tables["buy_fluxion_sell_bybit"]
    assert len(got.amm_buckets) == len(expected.amm_buckets)
    for a, b in zip(got.amm_buckets, expected.amm_buckets, strict=True):
        assert a.size_usd == b.size_usd
        assert a.fillable == b.fillable
        assert abs(a.pnl_usd - b.pnl_usd) < Decimal("1e-9")
        assert a.costs.bybit_fee_usd == b.costs.bybit_fee_usd
        assert a.costs.gas_usd == b.costs.gas_usd


def test_snapshot_stale_when_recv_old() -> None:
    pair = _load_aapl_pair()
    old = 1_700_000_000_000
    pool_tick = _pool_tick(ts=old)
    amm = amm_pool_from_tick(pair, pool_tick)
    snap = build_pnl_pair_snapshot(
        pair_id=pair.id,
        bybit=_book(ts=old),
        amm=amm,
        amm_tick=pool_tick,
        config=_cfg(),
        depth=_depth(ts=old),
        native_decimals=pair.fluxion.native_decimals,
        now_ms=old + 60_000,
        stale_ms=30_000,
    )
    assert snap.status == "stale"
    assert snap.tables == {}


def test_empty_reconstructed_depth_is_no_depth() -> None:
    """Journal depth row with all-None VWAPs must not claim has_depth."""
    pair = _load_aapl_pair()
    pool_tick = _pool_tick(mid=Decimal("99.5"))
    amm = amm_pool_from_tick(pair, pool_tick)
    depth = _depth(
        bid_vwap=(None, None, None, None, None, None),
        ask_vwap=(None, None, None, None, None, None),
    )
    snap = build_pnl_pair_snapshot(
        pair_id=pair.id,
        bybit=_book(),
        amm=amm,
        amm_tick=pool_tick,
        config=_cfg(gas=Decimal("0.01")),
        depth=depth,
        native_decimals=pair.fluxion.native_decimals,
    )
    assert snap.has_depth is False
    assert snap.status == "no_depth"


def test_checked_in_metrics_still_loads() -> None:
    # Smoke: wiring package does not break default config import.
    cfg = load_metrics_config()
    assert cfg.pnl_v2.buckets_usd[0] == Decimal(10)


def test_amm_pool_from_tick_geometry_matches_fixture() -> None:
    pair = _load_aapl_pair()
    tick = _pool_tick(mid=Decimal(100))
    amm = amm_pool_from_tick(pair, tick)
    assert amm is not None
    assert isinstance(amm, AmmPoolState)
    assert abs(amm.mid_quote_per_base() - Decimal(100)) / Decimal(100) < Decimal("0.01")
