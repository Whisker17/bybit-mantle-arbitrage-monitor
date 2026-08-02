"""Load Binance ⇄ Pancake bStocks inventory into BStocksPairsConfig."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from monitor.markets.load import market_file_path
from monitor.symbols.bstocks_models import BStocksPairsConfig

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_MARKET_ID = "binance-pancake"


class BStocksPairsConfigError(Exception):
    """Fail-fast error for missing or malformed bStocks inventory."""


def default_bstocks_pairs_path() -> Path:
    return market_file_path(_DEFAULT_MARKET_ID)


def _inventory_mapping(data: dict[str, Any], *, path: Path) -> dict[str, Any]:
    if "inventory" in data:
        inv = data["inventory"]
        if not isinstance(inv, dict):
            raise BStocksPairsConfigError(
                f"market inventory must be a mapping, got {type(inv).__name__} ({path})"
            )
        return inv
    return data


def load_bstocks_pairs_config(
    path: Path | None = None,
    *,
    market_id: str = _DEFAULT_MARKET_ID,
) -> BStocksPairsConfig:
    """Parse bStocks inventory YAML (market file or flat inventory body)."""
    config_path = path if path is not None else market_file_path(market_id)
    if not config_path.is_file():
        raise BStocksPairsConfigError(
            f"bStocks pairs config not found at {config_path}. "
            "Expected checked-in config/markets/binance-pancake.yaml."
        )
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BStocksPairsConfigError(
            f"cannot read bStocks pairs config at {config_path}: {exc}"
        ) from exc
    data: Any = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise BStocksPairsConfigError(
            f"bStocks pairs root must be a mapping, got {type(data).__name__} "
            f"({config_path})"
        )
    try:
        inv = _inventory_mapping(data, path=config_path)
        return BStocksPairsConfig.model_validate(inv)
    except BStocksPairsConfigError:
        raise
    except ValidationError as exc:
        raise BStocksPairsConfigError(
            f"invalid bStocks pairs config at {config_path}: {exc}"
        ) from exc
