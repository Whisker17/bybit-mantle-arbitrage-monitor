"""Seams: collector config load + MANTLE_RPC_URL rewrite."""

from __future__ import annotations

from monitor.collector.config import (
    default_collector_path,
    load_collector_config,
    resolve_mantle_rpc_url,
)


def test_load_checked_in_collector_config() -> None:
    path = default_collector_path()
    assert path.is_file()
    cfg = load_collector_config()
    assert cfg.version == 1
    assert cfg.bybit.book_topic_prefix == "orderbook.1"
    assert cfg.mantle.multicall3.lower().startswith("0xca11")
    assert cfg.rfq.amount_usdc_raw == "100000000"
    assert cfg.resolved_sqlite_path().name == "monitor.db"


def test_resolve_mantle_rpc_url_wss_rewrite(monkeypatch: object) -> None:
    monkeypatch.setenv(  # type: ignore[attr-defined]
        "MANTLE_RPC_URL", "wss://wss-tob.mantle.xyz/v1/secretkey"
    )
    assert resolve_mantle_rpc_url() == "https://rpc-tob.mantle.xyz/v1/secretkey"


def test_resolve_mantle_rpc_url_fallback(monkeypatch: object) -> None:
    monkeypatch.delenv("MANTLE_RPC_URL", raising=False)  # type: ignore[attr-defined]
    assert resolve_mantle_rpc_url("https://rpc.mantle.xyz") == "https://rpc.mantle.xyz"
