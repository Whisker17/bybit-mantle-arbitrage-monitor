"""Static-export pair paths must track config/pairs.yaml (WHI-758)."""

from __future__ import annotations

from pathlib import Path

import yaml

from monitor.symbols import load_pairs_config


def test_pairs_yaml_ids_match_symbols_loader() -> None:
    """GenerateStaticParams reads the same inventory the API serves."""
    root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((root / "config" / "pairs.yaml").read_text(encoding="utf-8"))
    yaml_ids = [p["id"] for p in raw["pairs"]]
    cfg_ids = [p.id for p in load_pairs_config().pairs]
    assert yaml_ids == cfg_ids
    assert len(yaml_ids) == 11


def test_web_pair_ids_ts_source_uses_pairs_yaml() -> None:
    """The TS loader must read config/pairs.yaml (not a hardcoded inventory)."""
    root = Path(__file__).resolve().parents[2]
    src = (root / "web" / "lib" / "pair-ids.ts").read_text(encoding="utf-8")
    assert "pairs.yaml" in src
    assert "loadPairIdsFromConfig" in src
    # No hand-maintained 11-id array literal left in the module.
    assert "AAPLx" not in src
