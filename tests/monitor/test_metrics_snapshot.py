"""Seam: build_spread_snapshot / build_edge_snapshot from quote ticks."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from monitor.metrics import SessionKind, load_metrics_config
from monitor.metrics.snapshot import build_edge_snapshot, build_spread_snapshot
from monitor.quotes import BybitBookTick, FluxionPoolStateTick, FluxionRfqQuoteTick

ET = ZoneInfo("America/New_York")


def _bybit(*, ts_ms: int) -> BybitBookTick:
    return BybitBookTick(
        pair_id="TSLAx",
        symbol="TSLAXUSDT",
        exchange_ts_ms=ts_ms,
        recv_ts_ms=ts_ms,
        bid=Decimal("100"),
        ask=Decimal("100.20"),
        bid_de_multiplied=Decimal("100"),
        ask_de_multiplied=Decimal("100.20"),
        multiplier=Decimal(1),
    )


def _amm() -> FluxionPoolStateTick:
    return FluxionPoolStateTick(
        pair_id="TSLAx",
        pool="0x" + "11" * 20,
        block_number=1,
        block_ts=1,
        recv_ts_ms=1,
        sqrt_price_x96=2**96,
        tick=0,
        liquidity=10**18,
        token0="0x" + "22" * 20,
        token1="0x" + "33" * 20,
        mid_usdc_per_wrapper=Decimal("99.5"),
        mid_usdc_per_native=Decimal("99.5"),
        wrapper_assets_per_share=Decimal(1),
    )


def _rfq(price: Decimal) -> FluxionRfqQuoteTick:
    return FluxionRfqQuoteTick(
        pair_id="TSLAx",
        poll_ts_ms=1,
        recv_ts_ms=1,
        token_in="0xusdc",
        token_out="0xtsla",
        amount_in="100000000",
        amount_out="1000000000000000000",
        price=price,
        side="buy",
        request_id="r1",
        http_status=200,
        available=True,
    )


def test_spread_snapshot_both_venues() -> None:
    cfg = load_metrics_config()
    # Monday 2026-08-03 10:00 ET = 14:00 UTC
    ts_ms = int(datetime(2026, 8, 3, 10, 0, tzinfo=ET).timestamp() * 1000)
    snap = build_spread_snapshot(
        bybit=_bybit(ts_ms=ts_ms),
        amm=_amm(),
        rfq=_rfq(Decimal("99.8")),
        config=cfg,
        ts_ms=ts_ms,
    )
    assert snap.session is SessionKind.OPEN
    assert snap.bybit_mid == Decimal("100.10")
    assert snap.amm_mid == Decimal("99.5")
    assert snap.rfq_mid == Decimal("99.8")
    assert snap.amm_spread_bps is not None
    assert snap.amm_spread_bps < 0  # AMM cheaper than Bybit
    assert snap.rfq_spread_bps is not None
    assert snap.rfq_spread_bps < 0


def test_edge_snapshot_rfq_and_amm_ladders() -> None:
    cfg = load_metrics_config()
    ts_ms = int(datetime(2026, 8, 3, 10, 0, tzinfo=ET).timestamp() * 1000)
    # Construct amm with known mid via mid_usdc_per_native; pool fee for ladder.
    # sqrt/liquidity still needed for slip — use deep pool.
    from monitor.metrics.amm_slip import mid_quote_per_base

    target = Decimal("99.5")
    ratio = Decimal(10) ** 12 / target
    sqrt_price_x96 = int(ratio.sqrt() * Decimal(2**96))
    mid = mid_quote_per_base(
        sqrt_price_x96,
        token0_is_quote=True,
        token0_decimals=6,
        token1_decimals=18,
    )
    amm = FluxionPoolStateTick(
        pair_id="TSLAx",
        pool="0x" + "11" * 20,
        block_number=1,
        block_ts=1,
        recv_ts_ms=ts_ms,
        sqrt_price_x96=sqrt_price_x96,
        tick=0,
        liquidity=10**18,
        token0="0x" + "22" * 20,
        token1="0x" + "33" * 20,
        mid_usdc_per_wrapper=mid,
        mid_usdc_per_native=mid,
        wrapper_assets_per_share=Decimal(1),
    )
    snap = build_edge_snapshot(
        bybit=_bybit(ts_ms=ts_ms),
        amm=amm,
        rfq=_rfq(Decimal("99.5")),
        config=cfg,
        pool_fee=3000,
        token0_is_quote=True,
        token0_decimals=6,
        token1_decimals=18,
        ts_ms=ts_ms,
    )
    assert len(snap.rfq_edges) == 6
    assert len(snap.amm_edges) == 6
    assert all(e.costs.fluxion_fee_bps == Decimal(30) for e in snap.amm_edges)
    assert all(e.costs.fluxion_fee_bps == Decimal(0) for e in snap.rfq_edges)
    # Best RFQ direction should be buy fluxion sell bybit with positive net
    best_rfq = max(
        (e for e in snap.rfq_edges if e.fillable),
        key=lambda e: e.net_edge_bps,
    )
    assert best_rfq.direction == "buy_fluxion_sell_bybit"
    assert best_rfq.net_edge_bps > 0
