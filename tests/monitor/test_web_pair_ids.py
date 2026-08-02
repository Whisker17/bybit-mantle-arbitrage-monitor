"""Static-export pair paths must track market inventory (WHI-758 / WHI-771 / WHI-774)."""

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
    """The TS loader must read config/markets/{id}.yaml (not a hand list)."""
    root = Path(__file__).resolve().parents[2]
    src = (root / "web" / "lib" / "pair-ids.ts").read_text(encoding="utf-8")
    assert "config" in src and "markets" in src
    assert "loadPairIdsFromConfig" in src
    assert "loadAllMarketPairParams" in src
    # No hand-maintained pair-id array literal left in the module.
    assert "AAPLx" not in src
    assert "TSLAB" not in src


def test_web_markets_client_module_has_no_fs() -> None:
    """Client-safe markets.ts must not import node:fs (static export)."""
    root = Path(__file__).resolve().parents[2]
    client = (root / "web" / "lib" / "markets.ts").read_text(encoding="utf-8")
    server = (root / "web" / "lib" / "markets-server.ts").read_text(encoding="utf-8")
    assert "from \"node:fs\"" not in client and "from 'node:fs'" not in client
    assert "from \"node:fs\"" in server or "from 'node:fs'" in server
    assert "loadKnownMarketsFromDisk" in server
    assert "marketOverviewPath" in client
