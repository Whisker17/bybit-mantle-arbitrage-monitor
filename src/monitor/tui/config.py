"""Typed TUI config (config/tui.yaml)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from monitor.metrics.config import MetricsConfig

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_TUI_PATH = _REPO_ROOT / "config" / "tui.yaml"

SortKey = Literal[
    "pair_id",
    "net_edge",
    "amm_spread",
    "rfq_spread",
    "bybit_mid",
    "volume_24h",
    "trades_24h",
]


class TuiConfigError(Exception):
    """Fail-fast error for missing or malformed TUI config."""


class TuiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    refresh_interval_s: float = Field(gt=0, le=60)
    # Default market id (M7-2). CLI --market overrides at process start.
    market: str = Field(default="bybit-fluxion", min_length=1)
    sqlite_path: str = Field(min_length=1)
    reference_size_usd: Decimal = Field(gt=0)
    default_sort: SortKey
    default_sort_desc: bool = True
    volume_window_ms: int = Field(ge=60_000)
    spread_history_max_points: int = Field(ge=10, le=10_000)
    trade_stream_limit: int = Field(ge=1, le=500)
    edge_history_max_samples: int = Field(ge=10, le=100_000)
    sparkline_width: int = Field(ge=8, le=200)

    @field_validator("reference_size_usd", mode="before")
    @classmethod
    def _to_decimal(cls, value: object) -> object:
        if isinstance(value, (int, float, str)):
            return Decimal(str(value))
        return value

    def resolved_sqlite_path(self, *, cwd: Path | None = None) -> Path:
        path = Path(self.sqlite_path)
        if path.is_absolute():
            return path
        base = cwd if cwd is not None else Path.cwd()
        return (base / path).resolve()


def default_tui_path() -> Path:
    return _DEFAULT_TUI_PATH


def load_tui_config(path: Path | None = None) -> TuiConfig:
    config_path = path if path is not None else default_tui_path()
    if not config_path.is_file():
        raise TuiConfigError(
            f"TUI config not found at {config_path}. "
            "Expected checked-in config/tui.yaml."
        )
    try:
        raw_text = config_path.read_text(encoding="utf-8")
        raw: Any = yaml.safe_load(raw_text)
    except (OSError, yaml.YAMLError) as exc:
        raise TuiConfigError(f"failed to read TUI config {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise TuiConfigError(f"TUI config root must be a mapping, got {type(raw)}")
    try:
        return TuiConfig.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise TuiConfigError(f"invalid TUI config {config_path}: {exc}") from exc


def validate_tui_against_metrics(tui: TuiConfig, metrics: MetricsConfig) -> None:
    """Fail fast when ``reference_size_usd`` cannot produce cumulative stats.

    ``EdgeStats.observe_edge`` only records samples at ``metrics.breach_size_usd``
    (M3), while the overview *Net* column and the detail edge panels both read
    ``tui.reference_size_usd`` — a mismatch silently yields empty distributions.
    Pure: raises ``TuiConfigError``, no I/O.
    """
    if tui.reference_size_usd not in metrics.size_ladder_usd:
        raise TuiConfigError(
            f"tui.reference_size_usd={tui.reference_size_usd} must be one of "
            f"metrics.size_ladder_usd={metrics.size_ladder_usd}"
        )
    if tui.reference_size_usd != metrics.breach_size_usd:
        raise TuiConfigError(
            f"tui.reference_size_usd={tui.reference_size_usd} must equal "
            f"metrics.breach_size_usd={metrics.breach_size_usd} "
            "(EdgeStats only accumulates the breach ladder rung)"
        )
