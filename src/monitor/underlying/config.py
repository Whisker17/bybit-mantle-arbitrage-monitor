"""Typed underlying-price config (config/underlying.yaml + collector toggle)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from monitor.metrics.config import SessionConfig

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PATH = _REPO_ROOT / "config" / "underlying.yaml"


class UnderlyingConfigError(Exception):
    """Fail-fast error for missing or malformed underlying config."""


class TickerFeedConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str = Field(min_length=1)
    pyth_symbol: str | None = None
    feed_id: str | None = None
    yahoo_symbol: str | None = None
    prefer_yahoo: bool = False
    uncovered: bool = False
    uncovered_reason: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> TickerFeedConfig:
        if self.uncovered:
            return self
        if self.prefer_yahoo and not self.yahoo_symbol:
            raise ValueError("prefer_yahoo requires yahoo_symbol")
        if not self.prefer_yahoo and not self.feed_id:
            raise ValueError("covered ticker requires feed_id or prefer_yahoo")
        return self


class UnderlyingConfig(BaseModel):
    """Shared underlying feed map + poll cadence (WHI-778)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    hermes_base_url: str = Field(min_length=1)
    open_poll_interval_s: float = Field(gt=0)
    closed_poll_interval_s: float = Field(gt=0)
    http_timeout_s: float = Field(gt=0)
    stale_after_open_ms: int = Field(ge=1)
    stale_after_abs_ms: int = Field(ge=1)
    stale_after_closed_ms: int = Field(ge=1)
    yahoo_fallback: bool = True
    yahoo_chart_base_url: str = Field(min_length=1)
    fx_usd_krw_feed_id: str | None = None
    session: SessionConfig
    tickers: dict[str, TickerFeedConfig] = Field(min_length=1)

    def covered_tickers(self) -> list[str]:
        return sorted(t for t, c in self.tickers.items() if not c.uncovered)

    def uncovered_tickers(self) -> list[str]:
        return sorted(t for t, c in self.tickers.items() if c.uncovered)

    def pyth_feed_ids(
        self,
        want: set[str] | None = None,
        *,
        include_fx: bool = False,
    ) -> list[str]:
        """Feed ids for Hermes batch. FX id only when ``include_fx`` is true."""
        ids: list[str] = []
        seen: set[str] = set()
        for name, cfg in self.tickers.items():
            if want is not None and name not in want:
                continue
            if cfg.uncovered or cfg.prefer_yahoo:
                continue
            if cfg.feed_id and cfg.feed_id not in seen:
                seen.add(cfg.feed_id)
                ids.append(cfg.feed_id)
        if include_fx and self.fx_usd_krw_feed_id and self.fx_usd_krw_feed_id not in seen:
            ids.append(self.fx_usd_krw_feed_id)
        return ids

    def needs_fx(self, want: set[str] | None = None) -> bool:
        """True when Hermes should batch ``fx_usd_krw_feed_id``.

        Requires a non-null FX feed id **and** at least one wanted
        ``prefer_yahoo`` ticker. Conversion still only applies when Yahoo meta
        currency is KRW (``parse_yahoo_chart``); USD ADRs ignore a present rate.
        """
        if not self.fx_usd_krw_feed_id:
            return False
        names = self.tickers.keys() if want is None else want
        return any(
            self.tickers[t].prefer_yahoo
            for t in names
            if t in self.tickers and not self.tickers[t].uncovered
        )

    def feed_id_to_ticker(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, cfg in self.tickers.items():
            if cfg.feed_id and not cfg.uncovered and not cfg.prefer_yahoo:
                key = cfg.feed_id.lower().removeprefix("0x")
                out[key] = name
        return out


def default_underlying_path() -> Path:
    return _DEFAULT_PATH


def load_underlying_config(path: Path | None = None) -> UnderlyingConfig:
    config_path = path if path is not None else default_underlying_path()
    if not config_path.is_file():
        raise UnderlyingConfigError(
            f"underlying config not found at {config_path}. "
            "Expected checked-in config/underlying.yaml."
        )
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise UnderlyingConfigError(
            f"cannot read underlying config at {config_path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise UnderlyingConfigError(
            f"underlying config root must be a mapping, got {type(raw).__name__}"
        )
    # Pydantic expects tickers as dict; YAML already is.
    data: dict[str, Any] = dict(raw)
    try:
        return UnderlyingConfig.model_validate(data)
    except Exception as exc:
        raise UnderlyingConfigError(
            f"invalid underlying config at {config_path}: {exc}"
        ) from exc
