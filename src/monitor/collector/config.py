"""Typed collector config (config/collector.yaml + .env for secrets)."""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# repo root = collector -> monitor -> src -> repo
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_COLLECTOR_PATH = _REPO_ROOT / "config" / "collector.yaml"
PUBLIC_RPC_URL = "https://rpc.mantle.xyz"


class CollectorConfigError(Exception):
    """Fail-fast error for missing or malformed collector config / env."""


class BybitDepthConfig(BaseModel):
    """Throttled multi-level VWAP journal (WHI-755 / PnL v2 Bybit leg)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    # Min wall-clock gap between depth rows per symbol (ms).
    emit_interval_ms: int = Field(default=1000, ge=50)
    # Also emit when mid moves by ≥ this many bps since last depth row (0 = off).
    # YAML may supply a number or string; stored as Decimal for consumers.
    mid_change_bps: Decimal = Field(default=Decimal("1"), ge=0)
    # USD notional ladder for precomputed bid/ask VWAP (PnL v2 buckets / DESIGN §2.6.3).
    buckets_usd: list[str] = Field(
        default_factory=lambda: ["10", "50", "100", "500", "1000", "10000"]
    )

    @field_validator("mid_change_bps", mode="before")
    @classmethod
    def _mid_as_decimal(cls, v: object) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"mid_change_bps must be a number, got {v!r}") from exc

    @model_validator(mode="after")
    def _buckets(self) -> BybitDepthConfig:
        if not self.buckets_usd:
            raise ValueError("bybit.depth.buckets_usd must be non-empty")
        for raw in self.buckets_usd:
            try:
                q = Decimal(str(raw))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"invalid bucket {raw!r}") from exc
            if q <= 0:
                raise ValueError(f"bucket must be > 0, got {raw!r}")
        return self


class BybitCollectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ws_url: str = Field(min_length=1)
    book_topic_prefix: str = Field(min_length=1)
    trade_topic_prefix: str = Field(min_length=1)
    reconnect_min_s: float = Field(gt=0)
    reconnect_max_s: float = Field(gt=0)
    post_reconnect_gap_s: float = Field(ge=0)
    ping_interval_s: float = Field(gt=0)
    depth: BybitDepthConfig = Field(default_factory=BybitDepthConfig)

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
    # Rolling window for block_ingest_latency_{p50,p95,p99}_ms meta (WHI-749).
    # Required in collector.yaml (no silent default — fail-fast like sibling fields).
    latency_window_blocks: int = Field(ge=1, le=10_000)

    @model_validator(mode="after")
    def _gap_bounds(self) -> MantleCollectorConfig:
        if self.max_block_gap > self.max_catchup_blocks:
            raise ValueError(
                f"max_block_gap={self.max_block_gap} must be <= "
                f"max_catchup_blocks={self.max_catchup_blocks}"
            )
        return self


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


class DiskGuardConfig(BaseModel):
    """Free-disk waterline for accelerated prune / write pause (WHI-751)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str | None = None
    warn_free_bytes: int = Field(ge=0)
    critical_free_bytes: int = Field(ge=0)
    warn_ttl_factor: float = Field(gt=0, le=1)
    critical_ttl_factor: float = Field(gt=0, le=1)
    pause_book_writes_on_critical: bool = True

    @model_validator(mode="after")
    def _levels(self) -> DiskGuardConfig:
        if self.critical_free_bytes > self.warn_free_bytes:
            raise ValueError(
                f"critical_free_bytes={self.critical_free_bytes} must be <= "
                f"warn_free_bytes={self.warn_free_bytes}"
            )
        if self.critical_ttl_factor > self.warn_ttl_factor:
            raise ValueError(
                f"critical_ttl_factor={self.critical_ttl_factor} must be <= "
                f"warn_ttl_factor={self.warn_ttl_factor}"
            )
        return self


class RetentionConfig(BaseModel):
    """SQLite retention policy — bounds journal growth (WHI-751 / DESIGN §5.1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    interval_s: float = Field(default=3600.0, gt=0)
    # None = never prune that table.
    bybit_book_raw_ms: int | None = Field(default=172_800_000, ge=1)  # 2d
    bybit_book_1m_ms: int | None = Field(default=1_209_600_000, ge=1)  # 14d
    bybit_depth_ms: int | None = Field(default=172_800_000, ge=1)  # 2d VWAP curve
    bybit_trades_ms: int | None = Field(default=604_800_000, ge=1)  # 7d
    fluxion_pool_state_ms: int | None = Field(default=604_800_000, ge=1)
    fluxion_rfq_quotes_ms: int | None = Field(default=259_200_000, ge=1)  # 3d
    fluxion_swaps_ms: int | None = Field(default=None, ge=1)
    fluxion_rfq_fills_ms: int | None = Field(default=None, ge=1)
    collector_gaps_ms: int | None = Field(default=2_592_000_000, ge=1)  # 30d
    delete_batch_size: int = Field(default=5000, ge=1, le=100_000)
    incremental_vacuum_pages: int = Field(default=1000, ge=0)
    full_vacuum: bool = False
    disk: DiskGuardConfig = Field(
        default_factory=lambda: DiskGuardConfig(
            path=None,
            warn_free_bytes=2_147_483_648,
            critical_free_bytes=1_073_741_824,
            warn_ttl_factor=0.25,
            critical_ttl_factor=0.05,
            pause_book_writes_on_critical=True,
        )
    )

    @model_validator(mode="after")
    def _bar_vs_raw(self) -> RetentionConfig:
        raw = self.bybit_book_raw_ms
        bars = self.bybit_book_1m_ms
        if raw is not None and bars is not None and bars < raw:
            raise ValueError(
                f"bybit_book_1m_ms={bars} must be >= bybit_book_raw_ms={raw} "
                "(bars cover the pruned raw window and beyond)"
            )
        return self


def default_retention_config() -> RetentionConfig:
    """Fallback when YAML omits ``retention:`` (tests / partial configs).

    Production values are the checked-in ``config/collector.yaml`` block —
    edit that file, not these Python field defaults, for deploy tuning.
    """
    return RetentionConfig()


class CollectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    sqlite_path: str = Field(min_length=1)
    bybit: BybitCollectorConfig
    mantle: MantleCollectorConfig
    rfq: RfqCollectorConfig
    logging: LoggingConfig
    retention: RetentionConfig = Field(default_factory=default_retention_config)

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
