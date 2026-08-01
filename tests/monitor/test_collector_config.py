"""Seams: collector config load + MANTLE_RPC_URL rewrite + cross-field validation."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from monitor.collector.config import (
    CollectorConfigError,
    default_collector_path,
    load_collector_config,
    resolve_mantle_rpc_url,
    rpc_url_kind,
)


def test_load_checked_in_collector_config() -> None:
    path = default_collector_path()
    assert path.is_file()
    cfg = load_collector_config()
    assert cfg.version == 1
    assert cfg.bybit.book_topic_prefix == "orderbook.1"
    assert cfg.mantle.multicall3.lower().startswith("0xca11")
    assert cfg.mantle.head_lag_blocks == 1
    assert cfg.rfq.amount_usdc_raw == "100000000"
    assert cfg.rfq.poll_both_sides is True
    assert cfg.resolved_sqlite_path().name == "monitor.db"
    assert cfg.retention.enabled is True
    assert cfg.retention.bybit_book_raw_ms == 172_800_000
    assert cfg.retention.fluxion_swaps_ms is None
    assert cfg.retention.disk.warn_free_bytes >= cfg.retention.disk.critical_free_bytes


def test_reconnect_max_must_ge_min(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(
        dedent(
            """
            version: 1
            sqlite_path: data/monitor.db
            bybit:
              ws_url: wss://example
              book_topic_prefix: orderbook.1
              trade_topic_prefix: publicTrade
              reconnect_min_s: 10
              reconnect_max_s: 1
              post_reconnect_gap_s: 1
              ping_interval_s: 20
            mantle:
              public_rpc_url: https://rpc.mantle.xyz
              multicall3: "0xca11bde05977b3631167028862be2a173976ca11"
              block_poll_interval_s: 0.5
              head_lag_blocks: 0
              max_block_gap: 1
              max_catchup_blocks: 15
              rpc_min_interval_s: 0.05
              rpc_timeout_s: 30
              rpc_retries: 5
              fetch_swap_receipts: true
            rfq:
              amount_usdc_raw: "100000000"
              amount_native_raw: "100000000000000000"
              prefer_primary_url: true
              poll_both_sides: true
              http_timeout_s: 20
            logging:
              level: INFO
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(CollectorConfigError, match="reconnect_max_s"):
        load_collector_config(p)


def test_retention_critical_must_le_warn(tmp_path: Path) -> None:
    p = tmp_path / "bad_disk.yaml"
    p.write_text(
        dedent(
            """
            version: 1
            sqlite_path: data/monitor.db
            bybit:
              ws_url: wss://example
              book_topic_prefix: orderbook.1
              trade_topic_prefix: publicTrade
              reconnect_min_s: 1
              reconnect_max_s: 10
              post_reconnect_gap_s: 1
              ping_interval_s: 20
            mantle:
              public_rpc_url: https://rpc.mantle.xyz
              multicall3: "0xca11bde05977b3631167028862be2a173976ca11"
              block_poll_interval_s: 0.5
              head_lag_blocks: 0
              max_block_gap: 1
              max_catchup_blocks: 15
              rpc_min_interval_s: 0.05
              rpc_timeout_s: 30
              rpc_retries: 5
              fetch_swap_receipts: true
            rfq:
              amount_usdc_raw: "100000000"
              amount_native_raw: "100000000000000000"
              prefer_primary_url: true
              poll_both_sides: true
              http_timeout_s: 20
            logging:
              level: INFO
            retention:
              enabled: true
              interval_s: 60
              disk:
                warn_free_bytes: 1000
                critical_free_bytes: 5000
                warn_ttl_factor: 0.5
                critical_ttl_factor: 0.1
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(CollectorConfigError, match="critical_free_bytes"):
        load_collector_config(p)


def test_resolve_mantle_rpc_url_wss_rewrite(monkeypatch: object) -> None:
    monkeypatch.setenv(  # type: ignore[attr-defined]
        "MANTLE_RPC_URL", "wss://wss-tob.mantle.xyz/v1/secretkey"
    )
    url = resolve_mantle_rpc_url()
    assert url == "https://rpc-tob.mantle.xyz/v1/secretkey"
    assert rpc_url_kind(url) == "keyed"


def test_resolve_mantle_rpc_url_fallback(monkeypatch: object) -> None:
    monkeypatch.delenv("MANTLE_RPC_URL", raising=False)  # type: ignore[attr-defined]
    assert resolve_mantle_rpc_url("https://rpc.mantle.xyz") == "https://rpc.mantle.xyz"
    assert rpc_url_kind("https://rpc.mantle.xyz") == "public"
