"""Seam: FastAPI read-only routes expose builder models as JSON."""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from monitor.api.app import create_app
from monitor.quotes import (
    BybitBookTick,
    BybitDepthTick,
    DexPoolTvlTick,
    FluxionPoolStateTick,
    now_ms,
)
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


def _write_api_yaml(
    tmp_path: Path,
    *,
    sqlite_path: Path,
    market: str = "bybit-fluxion",
) -> Path:
    """Minimal api.yaml for TestClient fixtures (cache TTLs off)."""
    api_yaml = tmp_path / "config" / "api.yaml"
    api_yaml.parent.mkdir(parents=True, exist_ok=True)
    api_yaml.write_text(
        f"""version: 1
host: 127.0.0.1
port: 8000
market: {market}
sqlite_path: {sqlite_path}
collector_stale_ms: 30000
recent_gap_window_ms: 300000
poll_interval_s: 2.0
pnl_cache_ttl_s: 0
mm_active_window_ms: 86400000
mm_series_max_points: 500
mm_rebalance_limit: 100
mm_inventory_cache_ttl_s: 0
cors_origins: []
""",
        encoding="utf-8",
    )
    return api_yaml


def _make_client(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.chdir(tmp_path)
    api_yaml = _write_api_yaml(tmp_path, sqlite_path=db)
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
    # Seeded depth reconstructs fillable levels + deep AMM → live optimal.
    assert pnl["has_depth"] is True
    assert pnl["status"] == "ok"
    assert pnl["optimal_net_pnl_usd"] is not None
    assert pnl["optimal_notional_usd"] is not None
    assert pnl["direction"] in (
        "buy_fluxion_sell_bybit",
        "buy_bybit_sell_fluxion",
    )
    # WHI-824: flat sort keys mirror ok numeric optimal (fixed-point strings).
    assert aapl["pnl_optimal_net_usd"] == pnl["optimal_net_pnl_usd"]
    assert aapl["pnl_optimal_net_bps"] == pnl["optimal_net_pnl_bps"]
    # WHI-966 / ADR-0002: Net at Q* — same bps/direction/size as Bucket PnL.
    assert aapl["net_edge_bps"] == pnl["optimal_net_pnl_bps"]
    assert aapl["net_edge_direction"] == pnl["direction"]
    assert aapl["net_edge_venue"] == "amm"
    assert aapl["net_size_usd"] == pnl["optimal_notional_usd"]
    # Sign agreement invariant (dual-ok tick).
    net = Decimal(aapl["net_edge_bps"])
    bucket = Decimal(aapl["pnl_optimal_net_bps"])
    assert (net > 0) == (bucket > 0)
    assert (net < 0) == (bucket < 0)
    assert (net == 0) == (bucket == 0)


def test_pairs_overview_flat_pnl_null_when_no_depth(client: TestClient) -> None:
    """WHI-824: non-ok pnl_v2 leaves flat sort fields None (nulls last / no Top-N)."""
    r = client.get("/api/pairs")
    assert r.status_code == 200
    aapl = next(row for row in r.json()["rows"] if row["pair_id"] == "AAPLx")
    assert aapl["pnl_v2"]["status"] == "no_depth"
    assert aapl["pnl_optimal_net_usd"] is None
    assert aapl["pnl_optimal_net_bps"] is None
    # WHI-966: no Q* → blank Net (do not fall back to M3 $1K).
    assert aapl["net_edge_bps"] is None
    assert aapl["net_edge_direction"] is None
    assert aapl["net_edge_venue"] is None
    assert aapl["net_size_usd"] is None


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
    assert "/api/pairs/{pair_id}/mm" in paths


def test_pairs_overview_mm_active_unknown_without_labels(client: TestClient) -> None:
    r = client.get("/api/pairs")
    assert r.status_code == 200
    aapl = next(row for row in r.json()["rows"] if row["pair_id"] == "AAPLx")
    assert aapl["mm_active"] == "unknown"


def test_pair_detail_address_panel_and_mm_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seed MM label + RFQ fill → address_panel + /mm inventory series."""
    db = tmp_path / "mm.db"
    _seed_store(db, with_depth=False)
    store = SqliteStore(db)
    ts = now_ms()
    maker = "0x" + "aa" * 20
    store.upsert_address_label(
        address=maker,
        label="market_maker",
        evidence_summary="n_rfq_maker>=2",
        first_seen_ms=ts - 60_000,
        last_seen_ms=ts,
        source="auto",
        is_rebalancer=False,
        n_rfq_maker=3,
        n_amm=0,
        cex_touch_transfers=0,
        updated_at_ms=ts,
    )
    from monitor.quotes import FluxionRfqFillTick

    store.insert_rfq_fills(
        [
            FluxionRfqFillTick(
                block_number=12345,
                block_ts=ts // 1000,
                recv_ts_ms=ts,
                tx_hash="0x" + "ab" * 32,
                log_index=0,
                order_hash="0x" + "cd" * 32,
                remaining_making_amount=0,
                pair_id="AAPLx",
                maker=maker,
                taker="0x" + "bb" * 20,
                direction="sell_native",
                making_token="0x" + "11" * 20,
                taking_token=USDC,
                making_amount="1.5",
                taking_amount="150",
                usdc_amount="150",
                stock_amount="1.5",
                enriched=True,
            )
        ]
    )
    store.insert_rebalance_events(
        [
            (
                maker,
                "0x" + "cc" * 20,
                "AAPLx",
                "0x" + "11" * 20,
                "2.0",
                "deposit_to_cex",
                12346,
                ts // 1000,
                ts,
                "0x" + "ee" * 32,
                1,
            )
        ]
    )
    store.close()

    with _make_client(db, tmp_path, monkeypatch) as client:
        detail = client.get("/api/pairs/AAPLx")
        assert detail.status_code == 200
        body = detail.json()
        assert "address_panel" in body
        assert isinstance(body["address_panel"], list)
        # Overview mm_active should be active (MM fill within 24h).
        assert body["overview"]["mm_active"] == "active"

        overview = client.get("/api/pairs")
        aapl = next(
            row for row in overview.json()["rows"] if row["pair_id"] == "AAPLx"
        )
        assert aapl["mm_active"] == "active"

        mm = client.get("/api/pairs/AAPLx/mm")
        assert mm.status_code == 200
        payload = mm.json()
        assert payload["pair_id"] == "AAPLx"
        assert payload["status"] == "ok"
        assert len(payload["addresses"]) >= 1
        addr = payload["addresses"][0]
        assert addr["address"] == maker.lower()
        assert addr["label"] == "market_maker"
        assert len(addr["series"]) >= 1
        assert len(payload["rebalance_events"]) >= 1
        assert payload["rebalance_events"][0]["direction"] == "deposit_to_cex"
        assert payload["rebalance_events"][0]["tx_hash"].startswith("0x")


# --- Multi-market (WHI-774) --------------------------------------------------


def test_list_markets_includes_both(client: TestClient) -> None:
    r = client.get("/api/markets")
    assert r.status_code == 200
    body = r.json()
    assert body["default_market_id"] == "bybit-fluxion"
    assert body["poll_interval_s"] == 2.0
    ids = {m["id"] for m in body["markets"]}
    assert "bybit-fluxion" in ids
    assert "binance-pancake" in ids
    bybit = next(m for m in body["markets"] if m["id"] == "bybit-fluxion")
    assert bybit["has_rfq"] is True
    assert bybit["data_status"] == "ok"
    assert bybit["pair_count"] >= 1
    assert "health" in bybit
    assert bybit["health"]["collector_alive"] is True
    bsc = next(m for m in body["markets"] if m["id"] == "binance-pancake")
    assert bsc["has_rfq"] is False
    # bStocks inventory is builder-ready (overview accepts BStocksPair).
    assert bsc["data_status"] == "ok"
    assert bsc["pair_count"] >= 1
    assert "health" in bsc


def test_market_scoped_pairs_matches_legacy(client: TestClient) -> None:
    legacy = client.get("/api/pairs")
    scoped = client.get("/api/bybit-fluxion/pairs")
    assert legacy.status_code == 200
    assert scoped.status_code == 200
    assert scoped.json()["market_id"] == "bybit-fluxion"
    assert scoped.json()["has_rfq"] is True
    assert scoped.json()["data_status"] == "ok"
    # Same pair set as unscoped default-market route.
    assert {r["pair_id"] for r in legacy.json()["rows"]} == {
        r["pair_id"] for r in scoped.json()["rows"]
    }


def test_market_scoped_health_and_detail(client: TestClient) -> None:
    h = client.get("/api/bybit-fluxion/health")
    assert h.status_code == 200
    assert h.json()["market_id"] == "bybit-fluxion"
    assert h.json()["collector_alive"] is True

    d = client.get("/api/bybit-fluxion/pairs/AAPLx")
    assert d.status_code == 200
    assert d.json()["pair_id"] == "AAPLx"
    assert d.json()["market_id"] == "bybit-fluxion"

    t = client.get("/api/bybit-fluxion/pairs/AAPLx/trades")
    assert t.status_code == 200
    assert t.json()["pair_id"] == "AAPLx"


def test_binance_pancake_no_longer_accumulating(client: TestClient) -> None:
    """bStocks is builder-ready: never the empty accumulating placeholder.

    Journal path is resolved against the real repo root (collector config), so
    a local dogfood ``data/monitor-binance-pancake.db`` yields 200 + rows;
    clean CI without that file yields 503 missing journal.
    """
    r = client.get("/api/binance-pancake/pairs")
    if r.status_code == 200:
        body = r.json()
        assert body["data_status"] == "ok"
        assert body["has_rfq"] is False
        assert isinstance(body["rows"], list)
        assert len(body["rows"]) >= 1
    else:
        assert r.status_code == 503
        assert "journal" in r.json()["detail"].lower()


def test_binance_pancake_overview_with_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seeded L1 book for a bStocks pair surfaces on the scoped overview.

    Collector sqlite paths resolve against the package repo root, so the test
    points ``_REPO_ROOT`` at ``tmp_path`` and places journals under
    ``tmp_path/data/`` — never writes into the real worktree ``data/``.
    """
    import monitor.markets.context as markets_context
    from monitor.symbols import load_bstocks_pairs_config

    bstocks = load_bstocks_pairs_config()
    pair = bstocks.pairs[0]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = data_dir / "monitor-binance-pancake.db"
    store = SqliteStore(db)
    ts = now_ms()
    store.set_meta("collector_started_ms", str(ts - 5_000))
    store.set_meta("market_id", "binance-pancake")
    store.insert_bybit_book(
        [
            BybitBookTick(
                pair_id=pair.id,
                symbol=pair.binance.symbol,
                exchange_ts_ms=ts,
                recv_ts_ms=ts,
                bid=Decimal("100"),
                ask=Decimal("100.20"),
                bid_de_multiplied=Decimal("100"),
                ask_de_multiplied=Decimal("100.20"),
                multiplier=pair.binance.ui_multiplier,
            )
        ]
    )
    store.close()

    bybit_db = data_dir / "monitor-bybit-fluxion.db"
    _seed_store(bybit_db, with_depth=False)
    monkeypatch.setattr(markets_context, "_REPO_ROOT", tmp_path)

    api_yaml = tmp_path / "config" / "api.yaml"
    api_yaml.parent.mkdir(parents=True, exist_ok=True)
    api_yaml.write_text(
        f"""version: 1
host: 127.0.0.1
port: 8000
market: bybit-fluxion
sqlite_path: {bybit_db}
collector_stale_ms: 30000
recent_gap_window_ms: 300000
poll_interval_s: 2.0
pnl_cache_ttl_s: 0
mm_active_window_ms: 86400000
mm_series_max_points: 500
mm_rebalance_limit: 100
mm_inventory_cache_ttl_s: 0
cors_origins: []
""",
        encoding="utf-8",
    )
    app = create_app(api_config_path=api_yaml)
    with TestClient(app) as c:
        r = c.get("/api/binance-pancake/pairs")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["market_id"] == "binance-pancake"
        assert body["data_status"] == "ok"
        assert body["has_rfq"] is False
        ids = {row["pair_id"] for row in body["rows"]}
        assert pair.id in ids
        row = next(x for x in body["rows"] if x["pair_id"] == pair.id)
        assert row["bybit_mid"] is not None
        assert row["stale"] is False
        # WHI-777 fields present (null CEX until poll; DEX zero without swaps).
        assert "cex_volume_24h" in row
        assert "dex_volume_24h" in row
        assert "volume_ratio" in row


def test_unknown_market_404(client: TestClient) -> None:
    r = client.get("/api/not-a-market/pairs")
    assert r.status_code == 404
    assert "unknown market" in r.json()["detail"]


def test_api_tvl_appears_after_late_table_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WHI-789: API-first start still surfaces TVL once collector creates the table.

    Mirrors production: journal open without ``dex_pool_tvl`` → overview probe →
    collector migrates + inserts → same long-lived reader must expose ``tvl_usd``
    on both markets without an API restart.
    """
    import monitor.markets.context as markets_context
    from monitor.symbols import load_bstocks_pairs_config

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bybit_db = data_dir / "monitor-bybit-fluxion.db"
    pancake_db = data_dir / "monitor-binance-pancake.db"
    _seed_store(bybit_db, with_depth=False)

    bstocks = load_bstocks_pairs_config()
    bpair = bstocks.pairs[0]
    store = SqliteStore(pancake_db)
    ts = now_ms()
    store.set_meta("collector_started_ms", str(ts - 5_000))
    store.set_meta("market_id", "binance-pancake")
    store.insert_bybit_book(
        [
            BybitBookTick(
                pair_id=bpair.id,
                symbol=bpair.binance.symbol,
                exchange_ts_ms=ts,
                recv_ts_ms=ts,
                bid=Decimal("100"),
                ask=Decimal("100.20"),
                bid_de_multiplied=Decimal("100"),
                ask_de_multiplied=Decimal("100.20"),
                multiplier=bpair.binance.ui_multiplier,
            )
        ]
    )
    store.close()

    # Table-absent at API start (not a version-gated pre-v7 migration — see
    # test_optional_table_accessors_refresh_after_late_create for the note).
    for db_path in (bybit_db, pancake_db):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DROP TABLE IF EXISTS dex_pool_tvl")
            conn.commit()
        finally:
            conn.close()

    monkeypatch.setattr(markets_context, "_REPO_ROOT", tmp_path)
    api_yaml = _write_api_yaml(tmp_path, sqlite_path=bybit_db)
    app = create_app(api_config_path=api_yaml)
    with TestClient(app) as client:
        r0 = client.get("/api/bybit-fluxion/pairs")
        assert r0.status_code == 200, r0.text
        aapl0 = next(x for x in r0.json()["rows"] if x["pair_id"] == "AAPLx")
        assert aapl0.get("tvl_usd") is None

        r1 = client.get("/api/binance-pancake/pairs")
        assert r1.status_code == 200, r1.text
        b0 = next(x for x in r1.json()["rows"] if x["pair_id"] == bpair.id)
        assert b0.get("tvl_usd") is None

        # Collector migrates + inserts after the first API probe.
        for pair_id, db_path, pool in (
            ("AAPLx", bybit_db, AAPL_POOL),
            (bpair.id, pancake_db, "0x" + "cd" * 20),
        ):
            with SqliteStore(db_path) as store:
                assert (
                    store.insert_pool_tvl(
                        [
                            DexPoolTvlTick(
                                pair_id=pair_id,
                                pool=pool,
                                block_number=99,
                                block_ts=ts // 1000,
                                recv_ts_ms=ts,
                                base_bal=Decimal("10"),
                                quote_bal=Decimal("1000"),
                                base_price=Decimal("100"),
                                tvl_usd=Decimal("2000"),
                                gap=False,
                            )
                        ]
                    )
                    == 1
                )

        r2 = client.get("/api/bybit-fluxion/pairs")
        assert r2.status_code == 200, r2.text
        aapl1 = next(x for x in r2.json()["rows"] if x["pair_id"] == "AAPLx")
        assert aapl1.get("tvl_usd") is not None
        assert Decimal(str(aapl1["tvl_usd"])) == Decimal("2000")

        r3 = client.get("/api/binance-pancake/pairs")
        assert r3.status_code == 200, r3.text
        b1 = next(x for x in r3.json()["rows"] if x["pair_id"] == bpair.id)
        assert b1.get("tvl_usd") is not None
        assert Decimal(str(b1["tvl_usd"])) == Decimal("2000")


def test_openapi_has_market_scoped_paths(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/markets" in paths
    assert "/api/{market}/health" in paths
    assert "/api/{market}/pairs" in paths
    assert "/api/{market}/pairs/{pair_id}" in paths
    assert "/api/{market}/pairs/{pair_id}/mm" in paths
    assert "/api/{market}/pairs/{pair_id}/trades" in paths
    # Legacy paths remain.
    assert "/api/health" in paths
    assert "/api/pairs" in paths


def test_builder_ready_market_missing_journal_returns_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Builder-ready market with no DB keeps pre-WHI-774 503 (not accumulating)."""
    monkeypatch.chdir(tmp_path)
    api_yaml = tmp_path / "config" / "api.yaml"
    api_yaml.parent.mkdir(parents=True, exist_ok=True)
    missing = tmp_path / "data" / "no-such-journal.db"
    api_yaml.write_text(
        f"""version: 1
host: 127.0.0.1
port: 8000
market: bybit-fluxion
sqlite_path: {missing}
collector_stale_ms: 30000
recent_gap_window_ms: 300000
poll_interval_s: 2.0
pnl_cache_ttl_s: 0
mm_active_window_ms: 86400000
mm_series_max_points: 500
mm_rebalance_limit: 100
mm_inventory_cache_ttl_s: 0
cors_origins: []
""",
        encoding="utf-8",
    )
    app = create_app(api_config_path=api_yaml)
    with TestClient(app) as client:
        r = client.get("/api/bybit-fluxion/pairs")
        assert r.status_code == 503
        assert "journal" in r.json()["detail"].lower()
        # Unscoped legacy path same contract.
        r2 = client.get("/api/pairs")
        assert r2.status_code == 503


def test_binance_pair_detail_builder_ready(client: TestClient) -> None:
    """bStocks detail is builder-ready (200 with journal, 503 without)."""
    r = client.get("/api/binance-pancake/pairs/TSLAB")
    if r.status_code == 200:
        body = r.json()
        assert body["pair_id"] == "TSLAB"
        assert body["market_id"] == "binance-pancake"
        assert body["data_status"] == "ok"
    else:
        assert r.status_code == 503
        assert "journal" in r.json()["detail"].lower()
