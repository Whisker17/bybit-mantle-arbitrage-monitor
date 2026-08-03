"""Typed API config (config/api.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from monitor.markets.ids import DEFAULT_MARKET_ID

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_API_PATH = _REPO_ROOT / "config" / "api.yaml"


class ApiConfigError(Exception):
    """Fail-fast error for missing or malformed API config."""


class ApiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    # Default market id (M7-2). CLI --market overrides at process start.
    market: str = Field(default=DEFAULT_MARKET_ID, min_length=1)
    sqlite_path: str = Field(min_length=1)
    # Process liveness only (/api/health collector_alive). Do not reuse as a
    # per-symbol quote wipe gate — quiet event-driven CEX books are normal.
    collector_stale_ms: int = Field(ge=1_000)
    # PnL v2 / quote annotation: legs older than this are marked quote_aged
    # but bucket tables still compute (WHI-821). Separate from collector_stale_ms.
    quote_max_age_ms: int = Field(default=300_000, ge=1_000)
    recent_gap_window_ms: int = Field(ge=1_000)
    poll_interval_s: float = Field(gt=0, le=60)
    # PnL v2 snapshot TTL (seconds). 0 disables cache (recompute every request).
    # Default slightly above poll_interval_s so steady pollers still hit cache.
    pnl_cache_ttl_s: float = Field(default=2.5, ge=0, le=60)
    # MM panel (WHI-769) — lookback, series caps, inventory event TTL.
    mm_active_window_ms: int = Field(default=86_400_000, ge=1_000)
    mm_series_max_points: int = Field(default=500, ge=10, le=10_000)
    mm_rebalance_limit: int = Field(default=100, ge=1, le=5_000)
    mm_inventory_cache_ttl_s: float = Field(default=2.5, ge=0, le=60)
    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _none_to_list(cls, value: object) -> object:
        return [] if value is None else value

    @model_validator(mode="after")
    def _cross_field(self) -> Self:
        # Poll slower than the stale window makes every healthy sample look dead
        # if clients only refresh at poll_interval_s (client-side lag aside).
        poll_ms = int(self.poll_interval_s * 1000)
        if poll_ms > self.collector_stale_ms:
            raise ValueError(
                f"poll_interval_s={self.poll_interval_s}s ({poll_ms}ms) must be "
                f"<= collector_stale_ms={self.collector_stale_ms}"
            )
        if self.recent_gap_window_ms < self.collector_stale_ms:
            raise ValueError(
                f"recent_gap_window_ms={self.recent_gap_window_ms} must be "
                f">= collector_stale_ms={self.collector_stale_ms}"
            )
        return self

    def resolved_sqlite_path(self, *, cwd: Path | None = None) -> Path:
        path = Path(self.sqlite_path)
        if path.is_absolute():
            return path
        base = cwd if cwd is not None else Path.cwd()
        return (base / path).resolve()


def default_api_path() -> Path:
    return _DEFAULT_API_PATH


def load_api_config(path: Path | None = None) -> ApiConfig:
    config_path = path if path is not None else default_api_path()
    if not config_path.is_file():
        raise ApiConfigError(
            f"API config not found at {config_path}. "
            "Expected checked-in config/api.yaml."
        )
    try:
        raw_text = config_path.read_text(encoding="utf-8")
        raw: Any = yaml.safe_load(raw_text)
    except (OSError, yaml.YAMLError) as exc:
        raise ApiConfigError(f"failed to read API config {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ApiConfigError(f"API config root must be a mapping, got {type(raw)}")
    try:
        return ApiConfig.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise ApiConfigError(f"invalid API config {config_path}: {exc}") from exc
