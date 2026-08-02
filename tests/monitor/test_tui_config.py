"""Seam: load_tui_config — typed fail-fast YAML."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from monitor.metrics import load_metrics_config
from monitor.tui.config import (
    TuiConfigError,
    load_tui_config,
    validate_tui_against_metrics,
)


def test_load_default_tui_config() -> None:
    cfg = load_tui_config()
    assert cfg.version == 1
    assert cfg.refresh_interval_s == 1.5
    assert cfg.reference_size_usd == Decimal(1000)
    assert cfg.default_sort == "net_edge"
    assert cfg.volume_window_ms == 86_400_000


def test_missing_config_fails(tmp_path: Path) -> None:
    with pytest.raises(TuiConfigError, match="not found"):
        load_tui_config(tmp_path / "nope.yaml")


def test_invalid_config_fails(tmp_path: Path) -> None:
    path = tmp_path / "tui.yaml"
    path.write_text("version: 1\nrefresh_interval_s: -1\n", encoding="utf-8")
    with pytest.raises(TuiConfigError, match="invalid"):
        load_tui_config(path)


def test_resolved_sqlite_path_relative(tmp_path: Path) -> None:
    cfg = load_tui_config()
    assert cfg.market == "bybit-fluxion"
    resolved = cfg.resolved_sqlite_path(cwd=tmp_path)
    assert resolved == (tmp_path / "data" / "monitor-bybit-fluxion.db").resolve()


def test_shipped_configs_agree_on_reference_size() -> None:
    validate_tui_against_metrics(load_tui_config(), load_metrics_config())


def test_reference_size_off_ladder_rejected() -> None:
    tui = load_tui_config()
    metrics = load_metrics_config()
    off_ladder = tui.model_copy(update={"reference_size_usd": Decimal(1234)})
    with pytest.raises(TuiConfigError, match="size_ladder_usd"):
        validate_tui_against_metrics(off_ladder, metrics)


def test_reference_size_must_equal_breach_size() -> None:
    tui = load_tui_config()
    metrics = load_metrics_config()
    other_rung = next(
        s for s in metrics.size_ladder_usd if s != metrics.breach_size_usd
    )
    mismatched = tui.model_copy(update={"reference_size_usd": other_rung})
    with pytest.raises(TuiConfigError, match="breach_size_usd"):
        validate_tui_against_metrics(mismatched, metrics)
