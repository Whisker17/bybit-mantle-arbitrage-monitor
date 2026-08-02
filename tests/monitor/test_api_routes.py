"""Seam: FastAPI read-only routes expose builder models as JSON."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from monitor.api.app import create_app
from monitor.quotes import BybitBookTick, BybitDepthTick, FluxionPoolStateTick, now_ms
from monitor.storage import SqliteStore

USDC = "0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9"
AAPL_POOL = "0x2cc6a607f3445d826b9e29f507b3a2e3b9dae106"
AAPL_TOKEN = "0x5aa7649fdbda47de64a07ac81d64b682af9c0724"

_BUCKETS = (
    Decimal(10),
    Decimal(50),
    Decimal(100),
    Decimal(500),
    Decimal(1000),
    Decimal(10000),
)


def _seed_store(db: Path, *, with_depth: bool) -> None:
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
    # sqrt aligned near mid ~99.5 so AMM is fillable for PnL v2.
    ratio = Decimal(10) ** 12 / Decimal("99.5")
    sqrt_price_x96 = int(ratio.sqrt() * Decimal(2**96))
    store.insert_pool_state(
        [
            FluxionPoolStateTick(
                pair_id="AAPLx",
                pool=AAPL_POOL,
                block_number=12345,
                block_ts=ts // 1000,
                recv_ts_ms=ts,
                sqrt_price_x96=sqrt_price_x96,
                tick=0,
                liquidity=10**20,
                token0=USDC,
                token1=AAPL_TOKEN,
                mid_usdc_per_wrapper=Decimal("99.5"),
                mid_usdc_per_native=Decimal("99.5"),
                wrapper_assets_per_share=Decimal(1),
            )
        ]
    )
    if with_depth:
        flat = tuple(Decimal("100") for _ in _BUCKETS)
        store.insert_bybit_depth(
            [
                BybitDepthTick(
                    pair_id="AAPLx",
                    symbol="AAPLXUSDT",
                    exchange_ts_ms=ts,
                    recv_ts_ms=ts,
                    bid=Decimal("100"),
                    ask=Decimal("100.20"),
                    bid_de_multiplied=Decimal("100"),
                    ask_de_multiplied=Decimal("100.20"),
                    multiplier=Decimal(1),
                    depth_levels=20,
                    buckets_usd=_BUCKETS,
                    bid_vwap_dm=flat,
                    ask_vwap_dm=flat,
                )
            ]
        )
    store.close()


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    db = tmp_path / "monitor.db"
    _seed_store(db, with_depth=False)
    return db


@pytest.fixture()
def seeded_db_with_depth(tmp_path: Path) -> Path:
    db = tmp_path / "monitor_depth.db"
    _seed_store(db, with_depth=True)
    return db


def _make_client(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.chdir(tmp_path)
    api_yaml = tmp_path / "config" / "api.yaml"
    api_yaml.parent.mkdir(parents=True, exist_ok=True)
    api_yaml.write_text(
        f"""version: 1
host: 127.0.0.1
port: 8000
sqlite_path: {db}
collector_stale_ms: 30000
recent_gap_window_ms: 300000
poll_interval_s: 2.0
pnl_cache_ttl_s: 0
cors_origins: []
""",
        encoding="utf-8",
    )
    app = create_app(api_config_path=api_yaml)
    return TestClient(app)


@pytest.fixture()
def client(seeded_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    with _make_client(seeded_db, tmp_path, monkeypatch) as c:
        yield c


@pytest.fixture()
def client_with_depth(
    seeded_db_with_depth: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    with _make_client(seeded_db_with_depth, tmp_path, monkeypatch) as c:
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
    # No depth row in seed → overview shows no_depth, not blank.
    assert "pnl_v2" in aapl
    assert aapl["pnl_v2"]["status"] == "no_depth"
    assert aapl["pnl_v2"]["has_depth"] is False
    assert aapl["pnl_v2"]["optimal_net_pnl_usd"] is None


def test_pair_detail(client: TestClient) -> None:
    r = client.get("/api/pairs/AAPLx")
    assert r.status_code == 200
    body = r.json()
    assert body["pair_id"] == "AAPLx"
    assert "overview" in body
    assert "edge_amm" in body
    assert "edge_rfq" in body
    assert "trades" in body
    assert "spread_series" in body
    assert isinstance(body["spread_series"], list)
    # WHI-759 chart fields: even empty series is a list; points carry session.
    if body["spread_series"]:
        pt = body["spread_series"][0]
        assert "ts_ms" in pt
        assert "amm_spread_bps" in pt
        assert "session" in pt
        assert "rfq_spread_bps" in pt or "bybit_mid" in pt
    assert "attribution" in body
    # Detail still exposes L1 bucket tables when depth is missing.
    assert "pnl_v2" in body
    pnl = body["pnl_v2"]
    assert pnl["has_depth"] is False
    assert "tables" in pnl
    assert "buy_fluxion_sell_bybit" in pnl["tables"]
    table = pnl["tables"]["buy_fluxion_sell_bybit"]
    assert len(table["amm_buckets"]) == 6
    for row in table["amm_buckets"]:
        assert "pnl_usd" in row
        assert "costs" in row
        assert "bybit_fee_usd" in row["costs"]


def test_pairs_overview_with_depth_exposes_optimal(client_with_depth: TestClient) -> None:
    r = client_with_depth.get("/api/pairs")
    assert r.status_code == 200
    aapl = next(row for row in r.json()["rows"] if row["pair_id"] == "AAPLx")
    pnl = aapl["pnl_v2"]
    assert pnl["status"] in ("ok", "no_fillable")
    assert pnl["has_depth"] is True
    if pnl["status"] == "ok":
        assert pnl["optimal_net_pnl_usd"] is not None
        assert pnl["optimal_notional_usd"] is not None
        assert pnl["direction"] in (
            "buy_fluxion_sell_bybit",
            "buy_bybit_sell_fluxion",
        )


def test_pair_detail_pnl_buckets_with_depth(client_with_depth: TestClient) -> None:
    r = client_with_depth.get("/api/pairs/AAPLx")
    assert r.status_code == 200
    pnl = r.json()["pnl_v2"]
    assert pnl["has_depth"] is True
    table = pnl["tables"]["buy_fluxion_sell_bybit"]
    sizes = [Decimal(b["size_usd"]) for b in table["amm_buckets"]]
    assert sizes == list(_BUCKETS)
    # Snapshot: every bucket has a numeric pnl string (may be negative).
    for b in table["amm_buckets"]:
        Decimal(b["pnl_usd"])  # parseable
        assert isinstance(b["fillable"], bool)
    if table["optimal"] is not None:
        Decimal(table["optimal"]["pnl_usd"])
        Decimal(table["optimal"]["q_star_usd"])


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
