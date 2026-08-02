"""Seams: bStocks inventory load + market context wiring (WHI-772)."""

from __future__ import annotations

from decimal import Decimal

from monitor.collector.config import load_collector_config
from monitor.markets import load_market_context
from monitor.symbols import load_bstocks_pairs_config


def test_load_checked_in_bstocks_inventory() -> None:
    cfg = load_bstocks_pairs_config()
    assert len(cfg.pairs) == 10
    assert cfg.rfq.mode == "none"
    assert cfg.contracts.chain_id == 56
    tsla = cfg.pair_by_id("TSLAB")
    assert tsla.binance.symbol == "TSLABUSDT"
    assert tsla.binance.ui_multiplier == Decimal("1")
    assert tsla.pancake.amm is not None
    assert tsla.pancake.amm.kind == "v3"
    mub = cfg.pair_by_id("MUB")
    assert mub.binance.ui_multiplier > 1
    assert len(cfg.pairs_with_amm()) == 10


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
    assert cfg.bsc.pool_state_every_n_blocks == 2
    assert cfg.bsc.quote_decimals == 18
    assert cfg.resolved_sqlite_path().name == "monitor-binance-pancake.db"
    assert "binance.vision" in cfg.binance.ws_base_url


def test_market_context_binance_pancake() -> None:
    ctx = load_market_context("binance-pancake")
    assert ctx.pairs is None
    assert ctx.bstocks is not None
    assert len(ctx.bstocks.pairs) == 10
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
