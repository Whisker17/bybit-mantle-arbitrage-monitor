"""Load and validate pair inventory into PairsConfig.

Default source (M7-2 / WHI-771): ``config/markets/bybit-fluxion.yaml``
``inventory:`` block. Explicit paths still accept:

* a market file (has top-level ``inventory:``), or
* a legacy flat pairs.yaml body (tests / one-off fixtures).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from monitor.markets.ids import DEFAULT_MARKET_ID
from monitor.markets.load import market_file_path
from monitor.symbols.models import PairsConfig

# repo root = parents: symbols -> monitor -> src -> repo
# Editable installs (uv/pip -e) resolve here; a non-editable wheel install
# needs an explicit path argument (M5 entrypoint can pass REPO/config/...).
_REPO_ROOT = Path(__file__).resolve().parents[3]
# Legacy path kept for error messages / discoverability (file removed after M7-2).
_LEGACY_PAIRS_PATH = _REPO_ROOT / "config" / "pairs.yaml"


class PairsConfigError(Exception):
    """Fail-fast error for missing or malformed pairs inventory config."""


def default_pairs_path() -> Path:
    """Default inventory path: market file for ``bybit-fluxion``."""
    return market_file_path(DEFAULT_MARKET_ID)


def _inventory_mapping(data: dict[str, Any], *, path: Path) -> dict[str, Any]:
    """Extract the PairsConfig-shaped mapping from a market file or flat YAML."""
    if "inventory" in data:
        inv = data["inventory"]
        if not isinstance(inv, dict):
            raise PairsConfigError(
                f"market inventory must be a mapping, got {type(inv).__name__} ({path})"
            )
        return inv
    return data


def load_pairs_config(
    path: Path | None = None,
    *,
    market_id: str | None = None,
) -> PairsConfig:
    """Parse pair inventory YAML and fail fast with a clear error on bad/missing data.

    * ``path`` — explicit file (market file or flat pairs body).
    * ``market_id`` — when ``path`` is None, load ``config/markets/{id}.yaml``
      inventory (default ``bybit-fluxion``).
    """
    if path is not None:
        config_path = path
    elif market_id is not None:
        config_path = market_file_path(market_id)
    else:
        config_path = default_pairs_path()

    if not config_path.is_file():
        raise PairsConfigError(
            f"pairs config not found at {config_path}. "
            "Expected checked-in config/markets/bybit-fluxion.yaml "
            "(or pass an explicit path / market_id to load_pairs_config)."
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
        inv = _inventory_mapping(data, path=config_path)
        return PairsConfig.model_validate(inv)
    except PairsConfigError:
        raise
    except ValidationError as exc:
        raise PairsConfigError(f"invalid pairs config at {config_path}: {exc}") from exc
