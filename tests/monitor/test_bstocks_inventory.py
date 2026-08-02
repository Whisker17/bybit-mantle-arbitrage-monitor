"""Seams: bStocks inventory load + market context wiring (WHI-772 / WHI-790)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from monitor.collector.config import load_collector_config
from monitor.markets import load_market_context
from monitor.metrics.amm_pool import _pair_pool_parts
from monitor.metrics.config import load_metrics_config
from monitor.quotes import BybitBookTick, now_ms
from monitor.symbols import load_bstocks_pairs_config
from monitor.tui.builder import build_pair_overview_row
from monitor.tui.config import load_tui_config

_REPO = Path(__file__).resolve().parents[2]
_ENUM_SNAPSHOT = _REPO / "docs" / "references" / "m7-bstocks-enum-snapshot.json"


def _expected_amm_ids() -> frozenset[str]:
    """Collector-scope set from the checked-in factory snapshot (single source)."""
    snap = json.loads(_ENUM_SNAPSHOT.read_text(encoding="utf-8"))
    return frozenset(snap["collector_scope_v3_usdt"])


def test_load_checked_in_bstocks_inventory() -> None:
    cfg = load_bstocks_pairs_config()
    assert len(cfg.pairs) == 55
    assert cfg.rfq.mode == "none"
    assert cfg.contracts.chain_id == 56
    assert cfg.version >= 1
    tsla = cfg.pair_by_id("TSLAB")
    assert tsla.binance.symbol == "TSLABUSDT"
    assert tsla.binance.ui_multiplier == Decimal("1")
    assert tsla.has_amm()
    assert tsla.dex_mode() == "amm"
    assert tsla.pancake.amm is not None
    assert tsla.pancake.amm.kind == "v3"
    mub = cfg.pair_by_id("MUB")
    assert mub.binance.ui_multiplier > 1
    expected = _expected_amm_ids()
    amm_ids = {p.id for p in cfg.pairs_with_amm()}
    assert amm_ids == expected
    assert len(cfg.pairs_dex_none()) == 55 - len(expected)
    # QQQB was a DexScreener false-positive under the old method; factory enum
    # with Binance capital BEP-20 verifies a real high-TVL USDT V3 pool.
    qqq = cfg.pair_by_id("QQQB")
    assert qqq.has_amm()
    assert qqq.pancake.amm is not None
    assert qqq.pancake.amm.fee == 100


def test_dex_none_pairs_have_token_registry_no_amm() -> None:
    cfg = load_bstocks_pairs_config()
    none_pairs = cfg.pairs_dex_none()
    assert none_pairs
    for p in none_pairs:
        assert p.dex_mode() == "none"
        assert not p.has_amm()
        assert p.pancake.amm is None
        assert p.low_liquidity is True
        # BEP-20 still recorded for the address registry.
        assert p.pancake.native_token.startswith("0x")
        assert len(p.pancake.native_token) == 42


def test_enum_snapshot_matches_inventory_amm_set() -> None:
    """Checked-in factory snapshot is the source of truth for AMM membership."""
    assert _ENUM_SNAPSHOT.is_file(), "run scripts/enumerate_bstocks_pools.py"
    snap = json.loads(_ENUM_SNAPSHOT.read_text(encoding="utf-8"))
    scope = set(snap.get("collector_scope_v3_usdt") or [])
    assert scope == _expected_amm_ids()
    assert snap["base_count"] == 55
    assert snap["collector_scope_count"] == len(scope)
    # Inventory pools must match snapshot addresses for scope pairs.
    cfg = load_bstocks_pairs_config()
    best = snap["best_by_base"]
    for pid in scope:
        pair = cfg.pair_by_id(pid)
        assert pair.pancake.amm is not None
        hit = best[pid]
        assert hit["kind"] == "v3" and hit["quote"] == "USDT"
        assert pair.pancake.amm.pool.lower() == hit["pool"].lower()
        assert pair.pancake.amm.fee == hit["fee"]


def test_dex_none_metrics_and_overview_do_not_error() -> None:
    """CEX-only pairs: pool geometry None, PnL no_pool, overview still builds."""
    from monitor.metrics.pnl_snapshot import build_pnl_pair_snapshot

    cfg = load_bstocks_pairs_config()
    none = cfg.pairs_dex_none()[0]
    assert none.pancake.amm is None
    assert _pair_pool_parts(none, quote_decimals=18) is None

    # Synthetic CEX book only — no pool tick.
    book = BybitBookTick(
        pair_id=none.id,
        symbol=none.binance.symbol,
        exchange_ts_ms=now_ms(),
        recv_ts_ms=now_ms(),
        bid=Decimal("100"),
        ask=Decimal("100.1"),
        bid_de_multiplied=Decimal("100"),
        ask_de_multiplied=Decimal("100.1"),
        multiplier=none.binance.ui_multiplier,
        gap=False,
    )
    metrics = load_metrics_config()
    tui = load_tui_config()
    row = build_pair_overview_row(
        none,
        bybit=book,
        amm=None,
        rfq_buy=None,
        rfq_sell=None,
        volume_24h=Decimal("0"),
        trades_24h=0,
        metrics=metrics,
        tui=tui,
        low_liquidity_threshold_usd=Decimal(
            str(cfg.low_liquidity_threshold_usd)
        ),
    )
    assert row.pair_id == none.id
    assert row.low_liquidity is True
    assert row.amm_mid is None
    assert row.bybit_mid is not None

    snap = build_pnl_pair_snapshot(
        pair_id=none.id,
        bybit=book,
        amm=None,
        amm_tick=None,
        depth=None,
        config=metrics,
        now_ms=now_ms(),
        rfq_enabled=False,
    )
    assert snap.status == "no_pool"


def test_collector_config_binance_pancake_shape() -> None:
    cfg = load_collector_config(market_id="binance-pancake")
    assert cfg.is_binance_pancake
    assert not cfg.is_bybit_fluxion
    assert cfg.binance is not None
    assert cfg.bsc is not None
    assert cfg.bybit is None
    assert cfg.rfq is None
    assert cfg.binance.book_stream == "bookTicker"
    assert cfg.binance.trade_stream == "aggTrade"
    assert cfg.binance.depth.enabled is True
    assert cfg.binance.depth.stream == "depth20@100ms"
    # Free-RPC default in collector.yaml (keyed RPC can lower this ops-side).
    assert cfg.bsc.pool_state_every_n_blocks == 8
    assert cfg.bsc.quote_decimals == 18
    assert cfg.resolved_sqlite_path().name == "monitor-binance-pancake.db"
    assert "binance.vision" in cfg.binance.ws_base_url


def test_market_context_binance_pancake() -> None:
    ctx = load_market_context("binance-pancake")
    assert ctx.pairs is None
    assert ctx.bstocks is not None
    assert len(ctx.bstocks.pairs) == 55
    assert len(ctx.bstocks.pairs_with_amm()) == len(_expected_amm_ids())
    assert ctx.collector is not None
    assert ctx.collector.is_binance_pancake
    assert ctx.sqlite_path.name == "monitor-binance-pancake.db"
    assert ctx.cex.multiplier_semantics.value == "multiply"
    assert ctx.dex.has_rfq is False


def test_market_context_bybit_still_has_pairs() -> None:
    ctx = load_market_context("bybit-fluxion")
    assert ctx.pairs is not None
    assert ctx.bstocks is None
    assert ctx.collector is not None
    assert ctx.collector.is_bybit_fluxion
