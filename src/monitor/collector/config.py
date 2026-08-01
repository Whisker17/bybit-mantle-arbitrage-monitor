"""Typed collector config (config/collector.yaml + .env for secrets)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# repo root = collector -> monitor -> src -> repo
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_COLLECTOR_PATH = _REPO_ROOT / "config" / "collector.yaml"
PUBLIC_RPC_URL = "https://rpc.mantle.xyz"


class CollectorConfigError(Exception):
    """Fail-fast error for missing or malformed collector config / env."""


class BybitCollectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ws_url: str = Field(min_length=1)
    book_topic_prefix: str = Field(min_length=1)
    trade_topic_prefix: str = Field(min_length=1)
    reconnect_min_s: float = Field(gt=0)
    reconnect_max_s: float = Field(gt=0)
    post_reconnect_gap_s: float = Field(ge=0)
    ping_interval_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _reconnect_bounds(self) -> BybitCollectorConfig:
        if self.reconnect_max_s < self.reconnect_min_s:
            raise ValueError(
                f"reconnect_max_s={self.reconnect_max_s} must be >= "
                f"reconnect_min_s={self.reconnect_min_s}"
            )
        return self


class MantleCollectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    public_rpc_url: str = Field(min_length=1)
    multicall3: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    block_poll_interval_s: float = Field(gt=0)
    head_lag_blocks: int = Field(ge=0)
    max_block_gap: int = Field(ge=1)
    max_catchup_blocks: int = Field(ge=1)
    rpc_min_interval_s: float = Field(ge=0)
    rpc_timeout_s: float = Field(gt=0)
    rpc_retries: int = Field(ge=1)
    fetch_swap_receipts: bool


class RfqCollectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    amount_usdc_raw: str = Field(min_length=1, pattern=r"^[0-9]+$")
    amount_native_raw: str = Field(min_length=1, pattern=r"^[0-9]+$")
    prefer_primary_url: bool
    poll_both_sides: bool
    http_timeout_s: float = Field(gt=0)


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class CollectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    sqlite_path: str = Field(min_length=1)
    bybit: BybitCollectorConfig
    mantle: MantleCollectorConfig
    rfq: RfqCollectorConfig
    logging: LoggingConfig

    def resolved_sqlite_path(self, repo_root: Path | None = None) -> Path:
        root = repo_root if repo_root is not None else _REPO_ROOT
        path = Path(self.sqlite_path)
        if path.is_absolute():
            return path
        return root / path


def default_collector_path() -> Path:
    return _DEFAULT_COLLECTOR_PATH


def load_dotenv(repo_root: Path | None = None) -> None:
    """Read .env without a dependency. Real process env always wins."""
    root = repo_root if repo_root is not None else _REPO_ROOT
    path = root / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def resolve_mantle_rpc_url(public_fallback: str = PUBLIC_RPC_URL) -> str:
    """Map optional MANTLE_RPC_URL (wss→https) to an HTTP JSON-RPC endpoint."""
    raw = os.environ.get("MANTLE_RPC_URL", "").strip()
    if not raw:
        return public_fallback
    if raw.startswith("wss://wss-"):
        return raw.replace("wss://wss-", "https://rpc-", 1)
    if raw.startswith("ws://ws-"):
        return raw.replace("ws://ws-", "http://rpc-", 1)
    if raw.startswith("wss://") or raw.startswith("ws://"):
        return "https://" + raw.split("://", 1)[1]
    return raw


def rpc_url_kind(url: str) -> Literal["keyed", "public"]:
    """Classify endpoint for meta/logging without hardcoding host fragments elsewhere."""
    # Mantle keyed tob endpoints rewrite to https://rpc-tob... (see resolve_mantle_rpc_url).
    if "rpc-tob." in url or "wss-tob." in url:
        return "keyed"
    if url.rstrip("/") == PUBLIC_RPC_URL.rstrip("/"):
        return "public"
    return "keyed" if "/v1/" in url else "public"


def load_collector_config(path: Path | None = None) -> CollectorConfig:
    config_path = path if path is not None else default_collector_path()
    if not config_path.is_file():
        raise CollectorConfigError(
            f"collector config not found at {config_path}. "
            "Expected checked-in config/collector.yaml."
        )
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CollectorConfigError(
            f"cannot read collector config at {config_path}: {exc}"
        ) from exc
    data: Any = yaml.safe_load(raw_text)
    if not isinstance(data, dict):
        raise CollectorConfigError(
            f"collector config root must be a mapping, got {type(data).__name__}"
        )
    try:
        return CollectorConfig.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise CollectorConfigError(
            f"invalid collector config at {config_path}: {exc}"
        ) from exc
