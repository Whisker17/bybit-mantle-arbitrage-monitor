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
    assert cfg.market == "bybit-fluxion"
    assert cfg.sqlite_path == "data/monitor-bybit-fluxion.db"
    assert cfg.collector_stale_ms == 30_000
    # WHI-821: quote annotation threshold, separate from process liveness.
    assert cfg.quote_max_age_ms == 300_000
    assert cfg.poll_interval_s == 2.0
    assert cfg.pnl_cache_ttl_s == 2.5
    # WHI-963: capture rate TTL (longer than PnL — trailing-window heavy).
    assert cfg.capture_cache_ttl_s == 30.0
    # Local Next dogfood origins (config/api.yaml); empty on VPS via deploy overlay.
    assert cfg.cors_origins == [
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ]
    # WHI-979: VPS static export path; mount only if the directory exists.
    assert cfg.static_dir == "/opt/xstocks/www"
    assert cfg.resolved_static_dir() == Path("/opt/xstocks/www")


def test_resolved_sqlite_path_relative(tmp_path: Path) -> None:
    cfg = load_api_config()
    resolved = cfg.resolved_sqlite_path(cwd=tmp_path)
    assert resolved == (tmp_path / "data" / "monitor-bybit-fluxion.db").resolve()


def test_resolved_static_dir_relative(tmp_path: Path) -> None:
    path = tmp_path / "api.yaml"
    path.write_text(
        """version: 1
host: 127.0.0.1
port: 8000
sqlite_path: data/monitor.db
collector_stale_ms: 30000
recent_gap_window_ms: 300000
poll_interval_s: 2.0
static_dir: web/out
cors_origins: []
""",
        encoding="utf-8",
    )
    cfg = load_api_config(path)
    assert cfg.static_dir == "web/out"
    assert cfg.resolved_static_dir(cwd=tmp_path) == (tmp_path / "web" / "out").resolve()


def test_empty_static_dir_is_none(tmp_path: Path) -> None:
    path = tmp_path / "api.yaml"
    path.write_text(
        """version: 1
host: 127.0.0.1
port: 8000
sqlite_path: data/monitor.db
collector_stale_ms: 30000
recent_gap_window_ms: 300000
poll_interval_s: 2.0
static_dir: ""
cors_origins: []
""",
        encoding="utf-8",
    )
    cfg = load_api_config(path)
    assert cfg.static_dir is None
    assert cfg.resolved_static_dir() is None


def test_missing_config_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(ApiConfigError, match="not found"):
        load_api_config(tmp_path / "missing.yaml")


def test_poll_cannot_exceed_stale_window(tmp_path: Path) -> None:
    path = tmp_path / "api.yaml"
    path.write_text(
        """version: 1
host: 127.0.0.1
port: 8000
sqlite_path: data/monitor.db
collector_stale_ms: 1000
recent_gap_window_ms: 300000
poll_interval_s: 5.0
cors_origins: []
""",
        encoding="utf-8",
    )
    with pytest.raises(ApiConfigError, match="poll_interval_s"):
        load_api_config(path)
