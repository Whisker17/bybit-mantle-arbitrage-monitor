"""Load and validate config/pairs.yaml into PairsConfig."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from monitor.symbols.models import PairsConfig

# repo root = parents: symbols -> monitor -> src -> repo
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PAIRS_PATH = _REPO_ROOT / "config" / "pairs.yaml"


def default_pairs_path() -> Path:
    return _DEFAULT_PAIRS_PATH


def load_pairs_config(path: Path | None = None) -> PairsConfig:
    """Parse pairs YAML and fail fast with a clear ValidationError on bad data."""
    config_path = path if path is not None else default_pairs_path()
    raw = config_path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"pairs config root must be a mapping, got {type(data).__name__}")
    return PairsConfig.model_validate(data)
