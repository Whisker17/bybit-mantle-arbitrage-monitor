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


def test_web_pair_ids_loader_script_parses_yaml() -> None:
    """Mirror the regex used by web/lib/pair-ids.ts loadPairIdsFromConfig."""
    root = Path(__file__).resolve().parents[2]
    text = (root / "config" / "pairs.yaml").read_text(encoding="utf-8")
    import re

    ids = re.findall(r"^\s+- id:\s+(\S+)\s*$", text, flags=re.MULTILINE)
    cfg_ids = [p.id for p in load_pairs_config().pairs]
    assert ids == cfg_ids
