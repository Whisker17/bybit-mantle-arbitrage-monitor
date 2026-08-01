"""Seam: config/api.yaml loads into a typed ApiConfig."""

from __future__ import annotations

from pathlib import Path

import pytest

from monitor.api.config import ApiConfigError, load_api_config


def test_load_default_api_config() -> None:
    cfg = load_api_config()
    assert cfg.version == 1
    assert cfg.port == 8000
    assert cfg.host == "127.0.0.1"
    assert cfg.sqlite_path == "data/monitor.db"
    assert cfg.collector_stale_ms == 30_000
    assert cfg.poll_interval_s == 2.0
    assert cfg.cors_origins == []


def test_resolved_sqlite_path_relative(tmp_path: Path) -> None:
    cfg = load_api_config()
    resolved = cfg.resolved_sqlite_path(cwd=tmp_path)
    assert resolved == (tmp_path / "data" / "monitor.db").resolve()


def test_missing_config_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(ApiConfigError, match="not found"):
        load_api_config(tmp_path / "missing.yaml")
