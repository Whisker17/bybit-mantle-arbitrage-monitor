"""WHI-773: M3/M4/PnL v2 on market=binance-pancake (same algorithms, market params).

Hand-recomputable fixtures: fixed Binance L1+depth + Pancake V3 pool state →
bucket PnL / mechanism share. Costs from config/markets/binance-pancake.yaml.
"""

from __future__ import annotations

from decimal import Decimal

from monitor.attribution import (
    build_attribution_snapshot,
    build_pair_attribution,
    load_attribution_config,
)
from monitor.attribution.events import AmmTradeEvent, RfqFillEvent
from monitor.fluxion.pools import mid_from_sqrt_price_x96
from monitor.markets import apply_market_attribution, load_market_context
from monitor.metrics import (
    amm_pool_from_pair_tick,
    amm_pool_from_tick,
    build_pnl_pair_snapshot,
    compute_pnl_usd,
    levels_from_depth_curve,
    pnl_bucket_table,
)
from monitor.metrics.amm_pool import AmmPoolState
from monitor.metrics.session import SessionKind
from monitor.quotes import (
    BybitBookTick,
    BybitDepthTick,
    FluxionPoolStateTick,
)
from monitor.symbols.bstocks_models import BStocksPair


def _deep_pool_at_mid(
    mid: Decimal,
    *,
    pool_fee: int,
    quote_decimals: int = 18,
    base_decimals: int = 18,
    liquidity: int = 10**24,
) -> AmmPoolState:
    """token0=quote, token1=base; deep enough that $10–$10k have sub-cent impact."""
    exp = base_decimals - quote_decimals
    ratio = (Decimal(10) ** exp) / mid
    sqrt_price_x96 = int(ratio.sqrt() * Decimal(2**96))
    got = mid_from_sqrt_price_x96(
        sqrt_price_x96,
        token0_is_quote=True,
        token0_decimals=quote_decimals,
        token1_decimals=base_decimals,
    )
    assert abs(got - mid) / mid < Decimal("0.001")
    return AmmPoolState(
        pool_fee=pool_fee,
        sqrt_price_x96=sqrt_price_x96,
        liquidity=liquidity,
        token0_is_quote=True,
        token0_decimals=quote_decimals,
        token1_decimals=base_decimals,
    )


def test_binance_pancake_costs_injected_into_metrics() -> None:
    ctx = load_market_context("binance-pancake", load_collector=False)
    assert ctx.metrics.bybit_taker_fee_bps == Decimal(10)
    assert ctx.metrics.gas_usd_per_swap == Decimal("0.05")
    assert ctx.metrics.usdt_usdc_basis_bps == Decimal(0)
    # Same algorithm knobs as shared metrics.yaml.
    assert ctx.metrics.pnl_v2.buckets_usd[0] == Decimal(10)
    assert ctx.metrics.pnl_v2.buckets_usd[-1] == Decimal(10000)


def test_attribution_has_rfq_false_from_market_not_id_branch() -> None:
    ctx = load_market_context("binance-pancake", load_collector=False)
    assert ctx.dex.has_rfq is False
    assert ctx.attribution.has_rfq is False

    ctx_bf = load_market_context("bybit-fluxion", load_collector=False)
    assert ctx_bf.dex.has_rfq is True
    assert ctx_bf.attribution.has_rfq is True

    # Explicit apply (config switch), no market-id string.
    base = load_attribution_config()
    assert base.has_rfq is True
    off = apply_market_attribution(base, has_rfq=False)
    assert off.has_rfq is False


def test_mechanism_layer_degenerates_to_all_amm_when_has_rfq_false() -> None:
    cfg = apply_market_attribution(load_attribution_config(), has_rfq=False)
    amm = [
        AmmTradeEvent(
            pair_id="TSLAB",
            ts_ms=1_700_000_000_000 + i,
            block_number=1000 + i,
            tx_hash=f"0x{i:064x}",
            log_index=i,
            taker="0x" + "aa" * 20,
            sender="0x" + "bb" * 20,
            direction="buy_native",
            notional_usd=Decimal(100),
            fluxion_mid_pre=Decimal(99),
            bybit_mid=Decimal(100),
            bybit_mid_prev=Decimal(100),
            session=SessionKind.OPEN,
        )
        for i in range(5)
    ]
    # Spurious RFQ fills must not count when has_rfq=false.
    rfq = [
        RfqFillEvent(
            ts_ms=1_700_000_100_000,
            block_number=2000,
            tx_hash="0xrfq" + "0" * 60,
            log_index=0,
            pair_id="TSLAB",
            order_hash="0xord",
            session=SessionKind.OPEN,
        )
    ]
    panel = build_pair_attribution(
        pair_id="TSLAB",
        amm_trades=amm,
        rfq_fills=rfq,
        config=cfg,
    )
    assert panel.mechanism.amm_trades == 5
    assert panel.mechanism.rfq_trades == 0
    assert panel.mechanism.amm_share == 1.0
    assert panel.mechanism.rfq_share == 0.0

    snap = build_attribution_snapshot(
        amm_trades=amm,
        rfq_fills=rfq,
        config=cfg,
        pair_ids=["TSLAB"],
    )
    assert snap.global_mechanism.rfq_trades == 0
    assert snap.global_mechanism.amm_trades == 5


def test_amm_pool_from_bstocks_pair_uses_usdt_18_decimals() -> None:
    ctx = load_market_context("binance-pancake", load_collector=False)
    assert ctx.bstocks is not None
    pair: BStocksPair = ctx.bstocks.pair_by_id("TSLAB")
    assert pair.pancake.amm is not None
    mid = Decimal(100)
    # token0 = USDT quote, token1 = native base (both 18d).
    exp = pair.pancake.native_decimals - 18
    ratio = (Decimal(10) ** exp) / mid
    sqrt_price_x96 = int(ratio.sqrt() * Decimal(2**96))
    tick = FluxionPoolStateTick(
        pair_id=pair.id,
        pool=pair.pancake.amm.pool,
        block_number=1,
        block_ts=1_700_000_000,
        recv_ts_ms=1_700_000_000_000,
        sqrt_price_x96=sqrt_price_x96,
        tick=0,
        liquidity=10**20,
        token0=pair.pancake.quote_token_address,
        token1=pair.pancake.native_token,
        mid_usdc_per_wrapper=mid,
        mid_usdc_per_native=mid,
        wrapper_assets_per_share=Decimal(1),
    )
    q_dec = ctx.dex.quote_decimals
    amm = amm_pool_from_pair_tick(pair, tick, quote_decimals=q_dec)
    assert amm is not None
    assert amm.pool_fee == pair.pancake.amm.fee  # TSLAB = 2500
    assert amm.token0_decimals == q_dec
    assert amm.token1_decimals == 18
    assert abs(amm.mid_quote_per_base() - mid) / mid < Decimal("0.01")

    # Pure component seam matches wrapper.
    pure = amm_pool_from_tick(
        tick,
        quote_token_address=pair.pancake.quote_token_address,
        pool_fee=pair.pancake.amm.fee,
        quote_decimals=q_dec,
        base_decimals=pair.pancake.native_decimals,
    )
    assert pure == amm


def test_binance_pancake_bucket_table_hand_recompute() -> None:
    """Fixed CEX mid + deep Pancake pool → fee/gas algebra is hand-checkable.

    Setup (buy AMM sell CEX):
      - CEX mid = 100 (comparable / multiplied space)
      - AMM mid = 100, pool_fee = 2500 (25 bps), deep liquidity
      - taker 10 bps, gas $0.05, basis 0
      - L1 flat book at mid

    At size Q=1000: q_base = 10
      USDT_recv ≈ 10 * 100 * 0.999 = 999
      USDT_spent ≈ 1000 * (1 + 25bps pool impact via fee) ≈ 1002.5 mid-aligned fee
      Plus gas 0.05 → clearly negative; gas line exact.
    """
    ctx = load_market_context("binance-pancake", load_collector=False)
    cfg = ctx.metrics
    mid = Decimal(100)
    pool_fee = 2500  # TSLAB inventory fee
    amm = _deep_pool_at_mid(mid, pool_fee=pool_fee)

    table = pnl_bucket_table(
        pair_id="TSLAB",
        bybit_bid=mid,
        bybit_ask=mid,
        direction="buy_fluxion_sell_bybit",
        config=cfg,
        amm=amm,
        include_optimal=False,
    )
    assert len(table.amm_buckets) == 6
    assert [r.size_usd for r in table.amm_buckets] == list(cfg.pnl_v2.buckets_usd)

    for row in table.amm_buckets:
        assert row.fillable
        assert row.costs.gas_usd == Decimal("0.05")
        # Mid-aligned: CEX fee = 0.001 * Q; pool fee ≈ 0.0025 * Q; gas 0.05.
        # Small rungs have near-zero UniV3 impact; $10k accrues measurable slip.
        cex_fee = Decimal("0.001") * row.size_usd
        pool_fee_approx = Decimal("0.0025") * row.size_usd
        fee_floor = -(cex_fee + pool_fee_approx + Decimal("0.05"))
        assert row.costs.bybit_fee_usd == cex_fee
        # Pool fee line within 1% of notional * 25 bps on deep book.
        assert abs(row.costs.fluxion_fee_usd - pool_fee_approx) < pool_fee_approx * Decimal(
            "0.02"
        )
        # PnL ≤ fee-only floor (extra slip is non-positive for this direction).
        assert row.pnl_usd <= fee_floor + Decimal("0.01")
        if row.size_usd <= Decimal(1000):
            assert abs(row.pnl_usd - fee_floor) < Decimal("0.15")

    # Single-size pure path matches table rung.
    r1000 = compute_pnl_usd(
        pair_id="TSLAB",
        bybit_bid=mid,
        bybit_ask=mid,
        size_usd=Decimal(1000),
        direction="buy_fluxion_sell_bybit",
        venue="amm",
        config=cfg,
        amm=amm,
    )
    assert r1000.fillable
    assert r1000.costs.gas_usd == Decimal("0.05")
    assert abs(r1000.pnl_usd - table.amm_buckets[4].pnl_usd) < Decimal("1e-9")


def test_binance_pancake_snapshot_with_depth_fixture() -> None:
    """Journal-shaped book + depth + pool → ok status, RFQ ignored when disabled."""
    ctx = load_market_context("binance-pancake", load_collector=False)
    cfg = ctx.metrics
    pair = ctx.bstocks.pair_by_id("TSLAB")  # type: ignore[union-attr]
    mid = Decimal(100)
    amm_mid = Decimal("99.5")
    ts = 1_700_000_000_000
    book = BybitBookTick(
        pair_id="TSLAB",
        symbol="TSLABUSDT",
        exchange_ts_ms=ts,
        recv_ts_ms=ts,
        bid=mid,
        ask=mid,
        # Collector multiplies display into comparable columns (BEP-677).
        bid_de_multiplied=mid,
        ask_de_multiplied=mid,
        multiplier=Decimal(1),
    )
    buckets = tuple(cfg.pnl_v2.buckets_usd)
    flat = tuple(mid for _ in buckets)
    depth = BybitDepthTick(
        pair_id="TSLAB",
        symbol="TSLABUSDT",
        exchange_ts_ms=ts,
        recv_ts_ms=ts,
        bid=mid,
        ask=mid,
        bid_de_multiplied=mid,
        ask_de_multiplied=mid,
        multiplier=Decimal(1),
        depth_levels=10,
        buckets_usd=buckets,
        bid_vwap_dm=flat,
        ask_vwap_dm=flat,
    )
    exp = pair.pancake.native_decimals - 18
    ratio = (Decimal(10) ** exp) / amm_mid
    sqrt_price_x96 = int(ratio.sqrt() * Decimal(2**96))
    pool_tick = FluxionPoolStateTick(
        pair_id="TSLAB",
        pool=pair.pancake.amm.pool,  # type: ignore[union-attr]
        block_number=1,
        block_ts=ts // 1000,
        recv_ts_ms=ts,
        sqrt_price_x96=sqrt_price_x96,
        tick=0,
        liquidity=10**24,
        token0=pair.pancake.quote_token_address,
        token1=pair.pancake.native_token,
        mid_usdc_per_wrapper=amm_mid,
        mid_usdc_per_native=amm_mid,
        wrapper_assets_per_share=Decimal(1),
    )
    amm = amm_pool_from_pair_tick(
        pair, pool_tick, quote_decimals=ctx.dex.quote_decimals
    )
    assert amm is not None

    snap = build_pnl_pair_snapshot(
        pair_id="TSLAB",
        bybit=book,
        amm=amm,
        amm_tick=pool_tick,
        config=cfg,
        depth=depth,
        rfq_enabled=ctx.attribution.has_rfq,
        native_decimals=pair.pancake.native_decimals,
    )
    assert snap.status == "ok"
    assert snap.has_depth is True
    assert len(snap.tables) == 2
    # Positive gross spread: buy AMM @ 99.5, sell CEX @ 100.
    buy_amm = snap.tables["buy_fluxion_sell_bybit"]
    assert buy_amm.amm_buckets[4].fillable
    # Gross ≈ 50 bps before wear; after 10+25 bps fees still positive at $1k.
    assert buy_amm.amm_buckets[4].pnl_usd > 0

    bids = levels_from_depth_curve(depth, side="bid")
    asks = levels_from_depth_curve(depth, side="ask")
    expected = pnl_bucket_table(
        pair_id="TSLAB",
        bybit_bid=mid,
        bybit_ask=mid,
        direction="buy_fluxion_sell_bybit",
        config=cfg,
        amm=amm,
        bybit_bids=bids,
        bybit_asks=asks,
        include_optimal=True,
    )
    for a, b in zip(buy_amm.amm_buckets, expected.amm_buckets, strict=True):
        assert abs(a.pnl_usd - b.pnl_usd) < Decimal("1e-9")


def test_cli_binance_pancake_smoke() -> None:
    from monitor.metrics.__main__ import main

    rc = main(
        [
            "--market",
            "binance-pancake",
            "--pair-id",
            "TSLAB",
            "--mid",
            "100",
            "--amm-mid",
            "99.5",
            "--json",
        ]
    )
    assert rc == 0


def test_cli_unknown_pair_fails_loud() -> None:
    from monitor.metrics.__main__ import main

    rc = main(
        [
            "--market",
            "binance-pancake",
            "--pair-id",
            "NOT_A_PAIR",
            "--json",
        ]
    )
    assert rc == 2


def test_comparable_mid_with_ui_multiplier_not_one() -> None:
    """BEP-677 multiply: journal comparable = display * mult; metrics use columns as-is."""
    from monitor.symbols.multipliers import multiplied_price

    ctx = load_market_context("binance-pancake", load_collector=False)
    cfg = ctx.metrics
    display = Decimal(100)
    mult = Decimal("1.0001075125688057")  # MUB inventory
    comparable = multiplied_price(display, mult)
    assert comparable != display
    amm = _deep_pool_at_mid(comparable, pool_fee=2500)
    r = compute_pnl_usd(
        pair_id="MUB",
        bybit_bid=comparable,
        bybit_ask=comparable,
        size_usd=Decimal(1000),
        direction="buy_fluxion_sell_bybit",
        venue="amm",
        config=cfg,
        amm=amm,
    )
    assert r.fillable
    # Mid-aligned on comparable space → negative fee/gas only (not 10× ghost arb).
    assert r.pnl_usd < 0
    assert abs(r.bybit_mid - comparable) < Decimal("1e-12")
