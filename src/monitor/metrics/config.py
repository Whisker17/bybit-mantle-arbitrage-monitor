"""Typed metrics config (config/metrics.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_METRICS_PATH = _REPO_ROOT / "config" / "metrics.yaml"


class MetricsConfigError(Exception):
    """Fail-fast error for missing or malformed metrics config."""


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timezone: str = Field(min_length=1)
    open: str = Field(pattern=r"^\d{2}:\d{2}$")
    close: str = Field(pattern=r"^\d{2}:\d{2}$")
    early_close: str = Field(pattern=r"^\d{2}:\d{2}$")

    def open_minutes(self) -> int:
        return _hhmm_to_minutes(self.open)

    def close_minutes(self) -> int:
        return _hhmm_to_minutes(self.close)

    def early_close_minutes(self) -> int:
        return _hhmm_to_minutes(self.early_close)

    @model_validator(mode="after")
    def _order(self) -> SessionConfig:
        o, c, e = self.open_minutes(), self.close_minutes(), self.early_close_minutes()
        if not (0 <= o < e <= c < 24 * 60):
            raise ValueError(
                f"session times must satisfy open < early_close <= close; "
                f"got open={self.open} early_close={self.early_close} close={self.close}"
            )
        return self


class MetricsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    size_ladder_usd: list[float] = Field(min_length=1)
    bybit_taker_fee_bps: float = Field(ge=0)
    usdt_usdc_basis_bps: float = 0.0
    gas_usd_per_swap: float = Field(ge=0)
    session: SessionConfig
    breach_size_usd: float = Field(gt=0)

    @field_validator("size_ladder_usd")
    @classmethod
    def _positive_sizes(cls, value: list[float]) -> list[float]:
        if any(s <= 0 for s in value):
            raise ValueError("size_ladder_usd entries must be > 0")
        if list(value) != sorted(value):
            raise ValueError("size_ladder_usd must be strictly ascending")
        if len(value) != len(set(value)):
            raise ValueError("size_ladder_usd must be unique")
        return value

    @model_validator(mode="after")
    def _breach_on_ladder(self) -> MetricsConfig:
        if self.breach_size_usd not in self.size_ladder_usd:
            raise ValueError(
                f"breach_size_usd={self.breach_size_usd} must be one of "
                f"size_ladder_usd={self.size_ladder_usd}"
            )
        return self


def _hhmm_to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    h, m = int(hours), int(minutes)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"invalid HH:MM time {hhmm!r}")
    return h * 60 + m


def default_metrics_path() -> Path:
    return _DEFAULT_METRICS_PATH


def load_metrics_config(path: Path | None = None) -> MetricsConfig:
    config_path = path if path is not None else default_metrics_path()
    if not config_path.is_file():
        raise MetricsConfigError(
            f"metrics config not found at {config_path}. "
            "Expected checked-in config/metrics.yaml."
        )
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MetricsConfigError(
            f"cannot read metrics config at {config_path}: {exc}"
        ) from exc
    data: Any = yaml.safe_load(raw_text)
    if not isinstance(data, dict):
        raise MetricsConfigError(
            f"metrics config root must be a mapping, got {type(data).__name__}"
        )
    try:
        return MetricsConfig.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise MetricsConfigError(
            f"invalid metrics config at {config_path}: {exc}"
        ) from exc
