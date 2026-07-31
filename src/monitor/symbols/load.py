"""Load and validate config/pairs.yaml into PairsConfig."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from monitor.symbols.models import PairsConfig

# repo root = parents: symbols -> monitor -> src -> repo
# Editable installs (uv/pip -e) resolve here; a non-editable wheel install
# needs an explicit path argument (M5 entrypoint can pass REPO/config/pairs.yaml).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PAIRS_PATH = _REPO_ROOT / "config" / "pairs.yaml"


class PairsConfigError(Exception):
    """Fail-fast error for missing or malformed pairs inventory config."""


def default_pairs_path() -> Path:
    return _DEFAULT_PAIRS_PATH


def load_pairs_config(path: Path | None = None) -> PairsConfig:
    """Parse pairs YAML and fail fast with a clear error on bad/missing data."""
    config_path = path if path is not None else default_pairs_path()
    if not config_path.is_file():
        raise PairsConfigError(
            f"pairs config not found at {config_path}. "
            "Expected checked-in config/pairs.yaml at the repo root "
            "(or pass an explicit path to load_pairs_config)."
        )
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PairsConfigError(f"cannot read pairs config at {config_path}: {exc}") from exc
    data: Any = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise PairsConfigError(
            f"pairs config root must be a mapping, got {type(data).__name__} ({config_path})"
        )
    try:
        return PairsConfig.model_validate(data)
    except Exception as exc:
        raise PairsConfigError(f"invalid pairs config at {config_path}: {exc}") from exc
