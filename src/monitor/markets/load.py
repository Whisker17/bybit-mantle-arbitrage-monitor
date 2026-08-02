"""Load market files from config/markets/."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from monitor.markets.ids import DEFAULT_MARKET_ID, normalize_market_id
from monitor.markets.models import MarketFile

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_MARKETS_DIR = _REPO_ROOT / "config" / "markets"


class MarketConfigError(Exception):
    """Fail-fast error for missing or malformed market config."""


def default_markets_dir() -> Path:
    return _DEFAULT_MARKETS_DIR


def market_file_path(market_id: str, *, markets_dir: Path | None = None) -> Path:
    mid = normalize_market_id(market_id)
    root = markets_dir if markets_dir is not None else default_markets_dir()
    return root / f"{mid}.yaml"


def list_market_ids(*, markets_dir: Path | None = None) -> list[str]:
    """Return sorted market ids that have a ``*.yaml`` file on disk."""
    root = markets_dir if markets_dir is not None else default_markets_dir()
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.yaml") if p.is_file())


def load_market_file(
    market_id: str = DEFAULT_MARKET_ID,
    *,
    path: Path | None = None,
    markets_dir: Path | None = None,
) -> MarketFile:
    """Parse a market YAML and fail fast on bad/missing data."""
    mid = normalize_market_id(market_id)
    config_path = path if path is not None else market_file_path(mid, markets_dir=markets_dir)
    if not config_path.is_file():
        raise MarketConfigError(
            f"market config not found at {config_path}. "
            f"Expected config/markets/{mid}.yaml (or pass an explicit path)."
        )
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MarketConfigError(
            f"cannot read market config at {config_path}: {exc}"
        ) from exc
    data: Any = yaml.safe_load(raw_text)
    if not isinstance(data, dict):
        raise MarketConfigError(
            f"market config root must be a mapping, got {type(data).__name__} "
            f"({config_path})"
        )
    try:
        mf = MarketFile.model_validate(data)
    except ValidationError as exc:
        raise MarketConfigError(
            f"invalid market config at {config_path}: {exc}"
        ) from exc
    if mf.id != mid and path is None:
        # When loading by id, file content must match the requested market.
        raise MarketConfigError(
            f"market file id={mf.id!r} does not match requested market_id={mid!r} "
            f"({config_path})"
        )
    return mf
