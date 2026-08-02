"""Static-export pair paths must track default market inventory (WHI-758 / WHI-771)."""

from __future__ import annotations

from pathlib import Path

import yaml

from monitor.symbols import load_pairs_config


def test_market_inventory_ids_match_symbols_loader() -> None:
    """GenerateStaticParams reads the same inventory the API serves."""
    root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load(
        (root / "config" / "markets" / "bybit-fluxion.yaml").read_text(encoding="utf-8")
    )
    inv = raw["inventory"] if "inventory" in raw else raw
    yaml_ids = [p["id"] for p in inv["pairs"]]
    cfg_ids = [p.id for p in load_pairs_config().pairs]
    assert yaml_ids == cfg_ids
    assert len(yaml_ids) == 11


def test_web_pair_ids_ts_source_uses_market_inventory() -> None:
    """The TS loader must read config/markets/bybit-fluxion.yaml (not a hand list)."""
    root = Path(__file__).resolve().parents[2]
    src = (root / "web" / "lib" / "pair-ids.ts").read_text(encoding="utf-8")
    assert "bybit-fluxion.yaml" in src
    assert "loadPairIdsFromConfig" in src
    # No hand-maintained 11-id array literal left in the module.
    assert "AAPLx" not in src
