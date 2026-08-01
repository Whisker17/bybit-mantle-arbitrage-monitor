"""Typed API config (config/api.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_API_PATH = _REPO_ROOT / "config" / "api.yaml"


class ApiConfigError(Exception):
    """Fail-fast error for missing or malformed API config."""


class ApiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    sqlite_path: str = Field(min_length=1)
    collector_stale_ms: int = Field(ge=1_000)
    recent_gap_window_ms: int = Field(ge=1_000)
    poll_interval_s: float = Field(gt=0, le=60)
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
