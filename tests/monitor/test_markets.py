"""Seams: multi-market assembly (M7-2 / WHI-771)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from textwrap import dedent

import pytest

from monitor.collector.config import load_collector_config
from monitor.markets import (
    DEFAULT_MARKET_ID,
    LEGACY_SQLITE_RELPATH,
    MarketConfigError,
    MultiplierSemantics,
    apply_market_costs,
    known_market_ids,
    list_market_ids,
    load_market_context,
    load_market_file,
    market_file_path,
    market_sqlite_relpath,
    normalize_market_id,
)
from monitor.markets.context import resolve_market_sqlite
from monitor.metrics import load_metrics_config
from monitor.symbols import load_pairs_config


def test_default_market_id_and_sqlite_convention() -> None:
    assert DEFAULT_MARKET_ID == "bybit-fluxion"
    assert market_sqlite_relpath("bybit-fluxion") == "data/monitor-bybit-fluxion.db"
    assert market_sqlite_relpath("binance_pancake") == "data/monitor-binance-pancake.db"
    assert normalize_market_id("Binance_Pancake") == "binance-pancake"
    assert LEGACY_SQLITE_RELPATH == "data/monitor.db"
    assert "bybit-fluxion" in known_market_ids()
    assert "binance-pancake" in known_market_ids()


def test_list_and_load_checked_in_markets() -> None:
    ids = list_market_ids()
    assert ids == ["binance-pancake", "bybit-fluxion"]

    bf = load_market_file("bybit-fluxion")
    assert bf.id == "bybit-fluxion"
    assert bf.cex.venue == "bybit"
    assert bf.cex.multiplier_semantics is MultiplierSemantics.DIVIDE
    assert bf.dex.has_rfq is True
    assert bf.dex.quote_decimals == 6
    assert bf.costs.cex_taker_fee_bps == Decimal(10)
    assert bf.costs.gas_usd_per_swap == Decimal("0.01")

    bp = load_market_file("binance-pancake")
    assert bp.id == "binance-pancake"
    assert bp.dex.quote_decimals == 18
    assert bp.cex.multiplier_semantics is MultiplierSemantics.MULTIPLY
    assert bp.dex.has_rfq is False
    assert bp.dex.chain_id == 56
    assert len(bp.inventory["pairs"]) == 55  # WHI-790 full bStocks universe


def test_load_pairs_from_default_market() -> None:
    """Bybit inventory still loads 11 pairs; path is markets/bybit-fluxion.yaml."""
    path = market_file_path(DEFAULT_MARKET_ID)
    assert path.name == "bybit-fluxion.yaml"
    assert path.is_file()
    cfg = load_pairs_config()
    assert len(cfg.pairs) == 11
    assert cfg.pair_by_id("TSLAx").bybit.symbol == "TSLAXUSDT"


def test_load_pairs_flat_fixture_still_works(tmp_path: Path) -> None:
    """Unit tests may still write a flat pairs body without market wrapper."""
    src = load_pairs_config()
    # Minimal re-dump of validated inventory.
    import yaml

    flat = {
        "version": 1,
        "inventory_as_of": str(src.inventory_as_of),
        "low_liquidity_threshold_usd": src.low_liquidity_threshold_usd,
        "contracts": src.contracts.model_dump(mode="json"),
        "rfq": src.rfq.model_dump(mode="json"),
        "pairs": [p.model_dump(mode="json") for p in src.pairs],
    }
    path = tmp_path / "pairs.yaml"
    path.write_text(yaml.dump(flat), encoding="utf-8")
    reloaded = load_pairs_config(path)
    assert {p.id for p in reloaded.pairs} == {p.id for p in src.pairs}


def test_collector_v2_market_section() -> None:
    cfg = load_collector_config(market_id="bybit-fluxion")
    assert cfg.version == 2
    assert cfg.sqlite_path == "data/monitor-bybit-fluxion.db"
    assert cfg.bybit is not None
    assert cfg.mantle is not None
    assert cfg.rfq is not None
    assert cfg.bybit.book_topic_prefix == "orderbook.50"
    assert cfg.mantle.head_lag_blocks == 1
    assert cfg.rfq.poll_both_sides is True
    assert cfg.resolved_sqlite_path().name == "monitor-bybit-fluxion.db"


def test_collector_v1_flat_fixture_still_works(tmp_path: Path) -> None:
    p = tmp_path / "collector.yaml"
    p.write_text(
        dedent(
            """
            version: 1
            sqlite_path: data/monitor.db
            bybit:
              ws_url: wss://example
              book_topic_prefix: orderbook.1
              trade_topic_prefix: publicTrade
              reconnect_min_s: 1
              reconnect_max_s: 10
              post_reconnect_gap_s: 1
              ping_interval_s: 20
            mantle:
              public_rpc_url: https://rpc.mantle.xyz
              multicall3: "0xca11bde05977b3631167028862be2a173976ca11"
              block_poll_interval_s: 0.5
              head_lag_blocks: 0
              max_block_gap: 1
              max_catchup_blocks: 15
              rpc_min_interval_s: 0.05
              rpc_timeout_s: 30
              rpc_retries: 5
              fetch_swap_receipts: true
              latency_window_blocks: 256
            rfq:
              amount_usdc_raw: "100000000"
              amount_native_raw: "100000000000000000"
              prefer_primary_url: true
              poll_both_sides: true
              http_timeout_s: 20
            logging:
              level: INFO
            """
        ),
        encoding="utf-8",
    )
    cfg = load_collector_config(p)
    assert cfg.version == 1
    assert cfg.sqlite_path == "data/monitor.db"


def test_collector_unknown_market_fails() -> None:
    with pytest.raises(Exception, match="no markets"):
        load_collector_config(market_id="does-not-exist")


def test_apply_market_costs_overrides_metrics() -> None:
    base = load_metrics_config()
    mf = load_market_file("binance-pancake")
    merged = apply_market_costs(base, mf.costs)
    assert merged.bybit_taker_fee_bps == mf.costs.cex_taker_fee_bps
    assert merged.gas_usd_per_swap == mf.costs.gas_usd_per_swap
    assert merged.usdt_usdc_basis_bps == mf.costs.quote_basis_bps
    # Algorithm knobs unchanged.
    assert merged.size_ladder_usd == base.size_ladder_usd
    assert merged.pnl_v2.buckets_usd == base.pnl_v2.buckets_usd


def test_apply_market_attribution_has_rfq_switch() -> None:
    from monitor.attribution import load_attribution_config
    from monitor.markets import apply_market_attribution

    base = load_attribution_config()
    assert base.has_rfq is True
    no_rfq = apply_market_attribution(base, has_rfq=False)
    assert no_rfq.has_rfq is False
    # Thresholds unchanged (retune via attribution_path, not market id).
    assert no_rfq.arb_bot.min_scored_trades == base.arb_bot.min_scored_trades


def test_load_market_context_bybit_fluxion() -> None:
    ctx = load_market_context("bybit-fluxion")
    assert ctx.market_id == "bybit-fluxion"
    assert ctx.pairs is not None
    assert len(ctx.pairs.pairs) == 11
    assert ctx.collector is not None
    assert ctx.attribution is not None
    assert ctx.metrics.bybit_taker_fee_bps == Decimal(10)
    # Convention path unless legacy fallback applies (checked separately).
    assert ctx.sqlite_path.name in {
        "monitor-bybit-fluxion.db",
        "monitor.db",
    }


def test_explicit_sqlite_does_not_legacy_fallback(tmp_path: Path) -> None:
    legacy = tmp_path / "data" / "monitor.db"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"")
    custom = tmp_path / "custom.db"
    resolved = resolve_market_sqlite(
        market_id="bybit-fluxion",
        configured=custom,
        repo_root=tmp_path,
        allow_legacy_fallback=True,  # still no redirect: custom ≠ convention
    )
    assert resolved == custom


def test_convention_path_falls_back_to_legacy(tmp_path: Path) -> None:
    """Config-derived convention path must still migrate from data/monitor.db."""
    legacy = tmp_path / "data" / "monitor.db"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"x")
    convention = tmp_path / "data" / "monitor-bybit-fluxion.db"
    resolved = resolve_market_sqlite(
        market_id="bybit-fluxion",
        configured=convention,
        repo_root=tmp_path,
        allow_legacy_fallback=True,
    )
    assert resolved == legacy.resolve()


def test_load_market_context_legacy_with_config_path(tmp_path: Path) -> None:
    """API/TUI pass yaml sqlite_path; legacy must still work mid-migration."""
    # Build a minimal market file + collector is heavy; exercise resolve via context
    # by pointing sqlite_path at convention under tmp_path with only legacy present.
    legacy = tmp_path / "data" / "monitor.db"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"")
    convention = tmp_path / "data" / "monitor-bybit-fluxion.db"
    ctx = load_market_context(
        "bybit-fluxion",
        sqlite_path=convention,
        repo_root=tmp_path,
        load_collector=False,
    )
    assert ctx.sqlite_path == legacy.resolve()


def test_load_market_context_binance_has_inventory_no_pairs_shape() -> None:
    ctx = load_market_context("binance-pancake", load_collector=False)
    assert ctx.market_id == "binance-pancake"
    assert ctx.pairs is None  # Bybit-shaped inventory not used
    assert ctx.bstocks is not None  # M7-3 bStocks inventory
    assert len(ctx.bstocks.pairs) == 55  # WHI-790 full bStocks universe
    assert ctx.cex.multiplier_semantics is MultiplierSemantics.MULTIPLY
    assert ctx.metrics.gas_usd_per_swap == Decimal("0.05")
    assert "binance-pancake" in str(ctx.sqlite_path)


def test_resolve_market_sqlite_legacy_fallback(tmp_path: Path) -> None:
    legacy = tmp_path / "data" / "monitor.db"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"")
    configured = tmp_path / "data" / "monitor-bybit-fluxion.db"
    resolved = resolve_market_sqlite(
        market_id="bybit-fluxion",
        configured=configured,
        repo_root=tmp_path,
    )
    assert resolved == legacy.resolve()

    # Non-default market never falls back to legacy.
    other = tmp_path / "data" / "monitor-binance-pancake.db"
    resolved2 = resolve_market_sqlite(
        market_id="binance-pancake",
        configured=other,
        repo_root=tmp_path,
    )
    assert resolved2 == other


def test_missing_market_file_fails(tmp_path: Path) -> None:
    with pytest.raises(MarketConfigError, match="not found"):
        load_market_file("bybit-fluxion", markets_dir=tmp_path)
