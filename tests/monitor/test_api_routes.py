"""Seam: FastAPI read-only routes expose builder models as JSON."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from monitor.api.app import create_app
from monitor.quotes import BybitBookTick, FluxionPoolStateTick, now_ms
from monitor.storage import SqliteStore

USDC = "0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9"
AAPL_POOL = "0x2cc6a607f3445d826b9e29f507b3a2e3b9dae106"
AAPL_TOKEN = "0x5aa7649fdbda47de64a07ac81d64b682af9c0724"


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    db = tmp_path / "monitor.db"
    store = SqliteStore(db)
    ts = now_ms()
    store.set_meta("collector_started_ms", str(ts - 5_000))
    store.set_meta("last_block", "12345")
    store.insert_bybit_book(
        [
            BybitBookTick(
                pair_id="AAPLx",
                symbol="AAPLXUSDT",
                exchange_ts_ms=ts,
                recv_ts_ms=ts,
                bid=Decimal("100"),
                ask=Decimal("100.20"),
                bid_de_multiplied=Decimal("100"),
                ask_de_multiplied=Decimal("100.20"),
                multiplier=Decimal(1),
            )
        ]
    )
    store.insert_pool_state(
        [
            FluxionPoolStateTick(
                pair_id="AAPLx",
                pool=AAPL_POOL,
                block_number=12345,
                block_ts=ts // 1000,
                recv_ts_ms=ts,
                sqrt_price_x96=2**96,
                tick=0,
                liquidity=10**18,
                token0=USDC,
                token1=AAPL_TOKEN,
                mid_usdc_per_wrapper=Decimal("99.5"),
                mid_usdc_per_native=Decimal("99.5"),
                wrapper_assets_per_share=Decimal(1),
            )
        ]
    )
    store.close()
    return db


@pytest.fixture()
def client(seeded_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Point default configs at the seeded journal via cwd resolution.
    monkeypatch.chdir(tmp_path)
    # Write a local api.yaml that points at the seeded DB filename used by default.
    api_yaml = tmp_path / "config" / "api.yaml"
    api_yaml.parent.mkdir(parents=True)
    # Override sqlite path to the absolute seeded db for this test.
    api_yaml.write_text(
        f"""version: 1
host: 127.0.0.1
port: 8000
sqlite_path: {seeded_db}
collector_stale_ms: 30000
recent_gap_window_ms: 300000
poll_interval_s: 2.0
cors_origins: []
""",
        encoding="utf-8",
    )
    # Pairs/metrics/attribution/tui still load from the real repo config paths
    # (defaults are absolute via Path(__file__) in those loaders).
    app = create_app(api_config_path=api_yaml)
    with TestClient(app) as c:
        yield c


def test_health_endpoint(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["db_exists"] is True
    assert body["last_block"] == 12345
    assert body["collector_alive"] is True
    assert body["ok"] is True
    assert "poll_interval_s" in body
    assert body["poll_interval_s"] == 2.0


def test_pairs_overview(client: TestClient) -> None:
    r = client.get("/api/pairs")
    assert r.status_code == 200
    body = r.json()
    assert "rows" in body
    assert "generated_ts_ms" in body
    assert "session_now" in body
    pair_ids = {row["pair_id"] for row in body["rows"]}
    assert "AAPLx" in pair_ids
    aapl = next(row for row in body["rows"] if row["pair_id"] == "AAPLx")
    assert aapl["stale"] is False
    assert aapl["bybit_mid"] is not None
    assert aapl["amm_mid"] is not None


def test_pair_detail(client: TestClient) -> None:
    r = client.get("/api/pairs/AAPLx")
    assert r.status_code == 200
    body = r.json()
    assert body["pair_id"] == "AAPLx"
    assert "overview" in body
    assert "edge_amm" in body
    assert "trades" in body


def test_pair_trades(client: TestClient) -> None:
    r = client.get("/api/pairs/AAPLx/trades")
    assert r.status_code == 200
    body = r.json()
    assert body["pair_id"] == "AAPLx"
    assert isinstance(body["trades"], list)
    assert "generated_ts_ms" in body


def test_unknown_pair_404(client: TestClient) -> None:
    r = client.get("/api/pairs/NOPE")
    assert r.status_code == 404


def test_openapi_available(client: TestClient) -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    paths = schema["paths"]
    assert "/api/health" in paths
    assert "/api/pairs" in paths
    assert "/api/pairs/{pair_id}" in paths
    assert "/api/pairs/{pair_id}/trades" in paths
