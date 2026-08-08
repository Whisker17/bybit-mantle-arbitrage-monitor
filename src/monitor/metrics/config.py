"""Typed metrics config (config/metrics.yaml)."""

from __future__ import annotations

from decimal import Decimal
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


class PnlV2Config(BaseModel):
    """Cash-flow PnL engine tunables (DESIGN §2.6 / hummingbot-pnl §5)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Fixed AMM display / table buckets (not the M3 TUI ladder).
    buckets_usd: list[Decimal] = Field(min_length=1)
    q_min_usd: Decimal = Field(gt=0)
    config_cap_usd: Decimal = Field(gt=0)
    coarse_points: int = Field(default=24, ge=2)
    refine_points: int = Field(default=16, ge=2)
    q_tol_rel: Decimal = Field(default=Decimal("1e-6"), gt=0)
    amm_solve_max_iters: int = Field(default=64, ge=1)
    amm_cap_max_iters: int = Field(default=24, ge=1)
    # Highlight / breach only — raw PnL is always emitted.
    min_profit_usd: Decimal | None = Field(default=None)
    min_profit_bps: Decimal | None = Field(default=None)
    gas_on_rfq: bool = True
    # Optional L1 soft depth cap (USD). None ⇒ depth_cap = +∞ on L1 path.
    l1_assumed_size_usd: Decimal | None = Field(default=None)

    @field_validator(
        "buckets_usd",
        "q_min_usd",
        "config_cap_usd",
        "q_tol_rel",
        "min_profit_usd",
        "min_profit_bps",
        "l1_assumed_size_usd",
        mode="before",
    )
    @classmethod
    def _to_decimal(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, list):
            return [Decimal(str(v)) for v in value]
        if isinstance(value, (int, float, str)):
            return Decimal(str(value))
        return value

    @field_validator("buckets_usd")
    @classmethod
    def _positive_buckets(cls, value: list[Decimal]) -> list[Decimal]:
        if any(s <= 0 for s in value):
            raise ValueError("pnl_v2.buckets_usd entries must be > 0")
        if list(value) != sorted(value):
            raise ValueError("pnl_v2.buckets_usd must be strictly ascending")
        if len(value) != len(set(value)):
            raise ValueError("pnl_v2.buckets_usd must be unique")
        return value

    @model_validator(mode="after")
    def _bounds(self) -> PnlV2Config:
        if self.q_min_usd > self.config_cap_usd:
            raise ValueError(
                f"pnl_v2.q_min_usd={self.q_min_usd} must be <= "
                f"config_cap_usd={self.config_cap_usd}"
            )
        if self.min_profit_usd is not None and self.min_profit_usd < 0:
            raise ValueError("pnl_v2.min_profit_usd must be >= 0 when set")
        if self.min_profit_bps is not None and self.min_profit_bps < 0:
            raise ValueError("pnl_v2.min_profit_bps must be >= 0 when set")
        if self.l1_assumed_size_usd is not None and self.l1_assumed_size_usd <= 0:
            raise ValueError("pnl_v2.l1_assumed_size_usd must be > 0 when set")
        return self


class MetricsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    size_ladder_usd: list[Decimal] = Field(min_length=1)
    bybit_taker_fee_bps: Decimal = Field(ge=0)
    # Additive wear on both directions; must be >= 0 (abs basis if measured negative).
    usdt_usdc_basis_bps: Decimal = Field(default=Decimal(0), ge=0)
    gas_usd_per_swap: Decimal = Field(ge=0)
    session: SessionConfig
    breach_size_usd: Decimal = Field(gt=0)
    # Max inter-sample gap counted toward breach duration (ms). Larger gaps
    # (overnight, collector restart) do not inflate session-segmented duration.
    max_breach_gap_ms: int = Field(default=300_000, ge=1)
    # PnL v2 cash-flow engine (WHI-756). Required — fail-fast at load (config/README).
    pnl_v2: PnlV2Config
    # WHI-822 / WHI-964: |AMM mid − CEX mid| / CEX in bps. Above this, mid/spread
    # still surface with reason ``pricing_anomaly`` but PnL optimal + paper edge
    # + Top-N seats are suppressed. Default 300 matches the executing bot's
    # tradability gate (DESIGN §2.6.5). None disables the guard.
    max_abs_amm_spread_bps: Decimal | None = Field(default=Decimal(300), ge=0)

    @field_validator(
        "size_ladder_usd",
        "bybit_taker_fee_bps",
        "usdt_usdc_basis_bps",
        "gas_usd_per_swap",
        "breach_size_usd",
        "max_abs_amm_spread_bps",
        mode="before",
    )
    @classmethod
    def _to_decimal(cls, value: object) -> object:
        if isinstance(value, list):
            return [Decimal(str(v)) for v in value]
        if isinstance(value, (int, float, str)):
            return Decimal(str(value))
        return value

    @field_validator("size_ladder_usd")
    @classmethod
    def _positive_sizes(cls, value: list[Decimal]) -> list[Decimal]:
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
