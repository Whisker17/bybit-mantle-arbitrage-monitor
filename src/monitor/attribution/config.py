"""Typed attribution config (config/attribution.yaml)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_ATTRIBUTION_PATH = _REPO_ROOT / "config" / "attribution.yaml"


class AttributionConfigError(Exception):
    """Fail-fast error for missing or malformed attribution config."""


class ArbBotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Minimum trades with a defined convergence flag (not raw n_trades).
    min_scored_trades: int = Field(ge=1)
    min_convergence_ratio: float = Field(ge=0.0, le=1.0)
    min_bybit_align_ratio: float = Field(default=0.0, ge=0.0, le=1.0)


class PriceKeeperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_trades: int = Field(ge=1)
    min_direction_share: float = Field(ge=0.0, le=0.5)
    max_median_notional_usd: Decimal = Field(gt=0)
    max_trade_notional_usd: Decimal = Field(gt=0)

    @field_validator(
        "max_median_notional_usd",
        "max_trade_notional_usd",
        mode="before",
    )
    @classmethod
    def _to_decimal(cls, value: object) -> object:
        if isinstance(value, (int, float, str)):
            return Decimal(str(value))
        return value

    @model_validator(mode="after")
    def _median_le_max_trade(self) -> PriceKeeperConfig:
        if self.max_median_notional_usd > self.max_trade_notional_usd:
            raise ValueError(
                "price_keeper.max_median_notional_usd must be "
                "<= max_trade_notional_usd"
            )
        return self


class RetailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_trades: int = Field(ge=1)


class ActivityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_closed_share_for_all_hours: float = Field(ge=0.0, le=1.0)
    min_trades_for_regime: int = Field(ge=1)


class BybitCorrelationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lookback_ms: int = Field(ge=1)
    min_move_bps: Decimal = Field(ge=0)

    @field_validator("min_move_bps", mode="before")
    @classmethod
    def _to_decimal(cls, value: object) -> object:
        if isinstance(value, (int, float, str)):
            return Decimal(str(value))
        return value


class AttributionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    top_takers_n: int = Field(ge=1)
    rpc_probe_batch_size: int = Field(ge=1, le=200)
    arb_bot: ArbBotConfig
    price_keeper: PriceKeeperConfig
    retail: RetailConfig
    activity: ActivityConfig
    bybit_correlation: BybitCorrelationConfig


def default_attribution_path() -> Path:
    return _DEFAULT_ATTRIBUTION_PATH


def load_attribution_config(path: Path | None = None) -> AttributionConfig:
    config_path = path if path is not None else default_attribution_path()
    if not config_path.is_file():
        raise AttributionConfigError(
            f"attribution config not found at {config_path}. "
            "Expected checked-in config/attribution.yaml."
        )
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AttributionConfigError(
            f"cannot read attribution config at {config_path}: {exc}"
        ) from exc
    data: Any = yaml.safe_load(raw_text)
    if not isinstance(data, dict):
        raise AttributionConfigError(
            f"attribution config root must be a mapping, got {type(data).__name__}"
        )
    try:
        return AttributionConfig.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise AttributionConfigError(
            f"invalid attribution config at {config_path}: {exc}"
        ) from exc
