"""Seam: overview/detail builders call M3/M4 (numbers must match)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from monitor.attribution import load_attribution_config
from monitor.metrics import best_net_edge, build_edge_snapshot, load_metrics_config
from monitor.metrics.session import SessionKind
from monitor.quotes import BybitBookTick, FluxionPoolStateTick, FluxionRfqQuoteTick
from monitor.storage import SqliteStore
from monitor.symbols import load_pairs_config
from monitor.tui.builder import (
    build_overview,
    build_pair_detail,
    build_pair_overview_row,
)
from monitor.tui.config import load_tui_config
from monitor.tui.format import sort_rows
from monitor.tui.model import PairOverviewRow, RunningEdgeState
from monitor.tui.pool import amm_pool_from_tick
from monitor.tui.reader import JournalReader

ET = ZoneInfo("America/New_York")
USDC = "0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9"


def _open_ts_ms() -> int:
    return int(datetime(2026, 8, 3, 10, 0, tzinfo=ET).timestamp() * 1000)


def _book(pair_id: str = "AAPLx", *, ts: int | None = None) -> BybitBookTick:
    ts_ms = ts if ts is not None else _open_ts_ms()
    return BybitBookTick(
        pair_id=pair_id,
        symbol=f"{pair_id.upper()}USDT",
        exchange_ts_ms=ts_ms,
        recv_ts_ms=ts_ms,
        bid=Decimal("100"),
        ask=Decimal("100.20"),
        bid_de_multiplied=Decimal("100"),
        ask_de_multiplied=Decimal("100.20"),
        multiplier=Decimal(1),
    )


def _amm(pair_id: str = "AAPLx", *, ts: int | None = None) -> FluxionPoolStateTick:
    ts_ms = ts if ts is not None else _open_ts_ms()
    return FluxionPoolStateTick(
        pair_id=pair_id,
        pool="0x2cc6a607f3445d826b9e29f507b3a2e3b9dae106",
        block_number=1,
        block_ts=ts_ms // 1000,
        recv_ts_ms=ts_ms,
        sqrt_price_x96=2**96,
        tick=0,
        liquidity=10**18,
        token0=USDC,
        token1="0x5aa7649fdbda47de64a07ac81d64b682af9c0724",
        mid_usdc_per_wrapper=Decimal("99.5"),
        mid_usdc_per_native=Decimal("99.5"),
        wrapper_assets_per_share=Decimal(1),
    )


def _rfq(
    pair_id: str, price: Decimal, side: str, *, ts: int | None = None
) -> FluxionRfqQuoteTick:
    ts_ms = ts if ts is not None else _open_ts_ms()
    return FluxionRfqQuoteTick(
        pair_id=pair_id,
        poll_ts_ms=ts_ms,
        recv_ts_ms=ts_ms,
        token_in="0xusdc",
        token_out="0xaapl",
        amount_in="100000000",
        amount_out="1",
        price=price,
        side=side,
        request_id="r",
        http_status=200,
        available=True,
    )


def test_overview_row_matches_m3_edge() -> None:
    pairs = load_pairs_config()
    pair = pairs.pair_by_id("AAPLx")
    metrics = load_metrics_config()
    tui = load_tui_config()
    bybit = _book()
    amm = _amm()
    rfq_buy = _rfq("AAPLx", Decimal("99.8"), "buy_native")
    rfq_sell = _rfq("AAPLx", Decimal("99.4"), "sell_native")
    pool = amm_pool_from_tick(pair, amm)
    assert pool is not None

    snap = build_edge_snapshot(
        bybit=bybit,
        config=metrics,
        amm=amm,
        amm_pool=pool,
        rfq_buy=rfq_buy,
        rfq_sell=rfq_sell,
        ts_ms=_open_ts_ms(),
    )
    combined = list(snap.amm_edges) + list(snap.rfq_edges)
    ref = tui.reference_size_usd
    expected = best_net_edge([e for e in combined if e.size_usd == ref])

    row = build_pair_overview_row(
        pair,
        bybit=bybit,
        amm=amm,
        rfq_buy=rfq_buy,
        rfq_sell=rfq_sell,
        volume_24h=Decimal(0),
        trades_24h=0,
        metrics=metrics,
        tui=tui,
        ts_ms=_open_ts_ms(),
    )
    assert row.session is SessionKind.OPEN
    assert row.bybit_mid == snap.spreads.bybit_mid
    assert row.amm_spread_bps == snap.spreads.amm_spread_bps
    assert expected is not None
    assert row.net_edge_bps == expected.net_edge_bps
    assert row.net_edge_venue == expected.venue
    assert row.net_edge_direction == expected.direction
    assert not row.low_liquidity


def test_amm_pool_from_tick_token_order() -> None:
    pairs = load_pairs_config()
    pair = pairs.pair_by_id("AAPLx")
    pool = amm_pool_from_tick(pair, _amm())
    assert pool is not None
    assert pool.token0_is_quote is True
    assert pool.pool_fee == 3000


def test_sort_rows_net_edge_desc() -> None:
    def row(pid: str, edge: Decimal | None, low: bool = False) -> PairOverviewRow:
        return PairOverviewRow(
            pair_id=pid,
            name=pid,
            low_liquidity=low,
            session=SessionKind.OPEN,
            bybit_bid=None,
            bybit_ask=None,
            bybit_mid=None,
            amm_mid=None,
            rfq_buy=None,
            rfq_sell=None,
            amm_spread_bps=None,
            rfq_spread_bps=None,
            net_edge_bps=edge,
            net_edge_venue=None,
            net_edge_direction=None,
            reference_size_usd=Decimal(1000),
            volume_24h=Decimal(0),
            trades_24h=0,
        )

    rows = [row("a", Decimal(1)), row("b", Decimal(5)), row("c", None)]
    sorted_rows = sort_rows(rows, key="net_edge", desc=True)
    assert [r.pair_id for r in sorted_rows] == ["b", "a", "c"]


def test_overview_and_detail_from_sqlite(tmp_path: Path) -> None:
    pairs = load_pairs_config()
    metrics = load_metrics_config()
    attr = load_attribution_config()
    tui = load_tui_config()
    db = tmp_path / "m.db"
    store = SqliteStore(db)
    ts = _open_ts_ms()
    store.insert_bybit_book([_book(ts=ts), _book(ts=ts + 1000)])
    store.insert_pool_state([_amm(ts=ts), _amm(ts=ts + 1000)])
    store.insert_rfq_quotes(
        [
            _rfq("AAPLx", Decimal("99.8"), "buy_native", ts=ts),
            _rfq("AAPLx", Decimal("99.4"), "sell_native", ts=ts),
        ]
    )
    store.close()

    with JournalReader(db) as reader:
        state = RunningEdgeState()
        # App path: overview first (seeds live ticks), then detail rebuilds history.
        overview = build_overview(
            pairs=pairs,
            reader=reader,
            metrics=metrics,
            tui=tui,
            now=ts + 2000,
            edge_state=state,
        )
        assert overview.rows
        aapl = next(r for r in overview.rows if r.pair_id == "AAPLx")
        assert aapl.bybit_mid is not None
        assert aapl.amm_spread_bps is not None
        assert aapl.pair_id in {k[0] for k in state.stats}  # warmed
        assert "AAPLx" not in state.history_rebuilt

        detail = build_pair_detail(
            pair=pairs.pair_by_id("AAPLx"),
            reader=reader,
            metrics=metrics,
            attribution_cfg=attr,
            tui=tui,
            edge_state=state,
            now=ts + 2000,
        )
        assert detail.pair_id == "AAPLx"
        assert detail.overview.bybit_mid == aapl.bybit_mid
        assert detail.spread_series  # history present
        assert "AAPLx" in state.history_rebuilt
        # Edge panels use M3 EdgeStats — distribution count grows with history.
        assert detail.edge_amm.distribution_all.count >= 1

        # Second detail open must not double-count history.
        n1 = detail.edge_amm.distribution_all.count
        detail2 = build_pair_detail(
            pair=pairs.pair_by_id("AAPLx"),
            reader=reader,
            metrics=metrics,
            attribution_cfg=attr,
            tui=tui,
            edge_state=state,
            now=ts + 2000,
        )
        assert detail2.edge_amm.distribution_all.count == n1
