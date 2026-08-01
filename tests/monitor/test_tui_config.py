"""Seam: load_tui_config — typed fail-fast YAML."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from monitor.tui.config import TuiConfigError, load_tui_config


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
    resolved = cfg.resolved_sqlite_path(cwd=tmp_path)
    assert resolved == (tmp_path / "data" / "monitor.db").resolve()
