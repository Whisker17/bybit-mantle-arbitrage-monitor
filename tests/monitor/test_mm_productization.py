"""WHI-768: RFQ enrichment schema, transfers, address labels, rebalance events."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from eth_utils import keccak  # type: ignore[attr-defined]

from monitor.attribution import (
    BehaviorLabel,
    assign_address_label,
    label_addresses_from_journal,
    load_attribution_config,
    rebalance_events_from_transfers,
)
from monitor.attribution.mm_draft import DraftAddressFeatures
from monitor.collector.backfill_rfq import enrich_fill_from_receipt
from monitor.fluxion.events import decode_erc20_transfer_log
from monitor.quotes import Erc20TransferTick, FluxionRfqFillTick
from monitor.storage import SqliteStore
from monitor.storage.schema import SCHEMA_VERSION
from monitor.symbols import load_pairs_config
from monitor.symbols.token_map import inventory_token_to_pair, native_token_to_pair

TOPIC_TRANSFER = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
TOPIC_APPROVAL = "0x" + keccak(text="Approval(address,address,uint256)").hex()


def _addr(n: int) -> str:
    return "0x" + f"{n:040x}"


def test_schema_v4_bootstrap(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "v4.db")
    assert store.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert SCHEMA_VERSION >= 4
    cols = {
        r[1]
        for r in store._conn.execute("PRAGMA table_info(fluxion_rfq_fills)").fetchall()
    }
    for name in ("maker", "taker", "pair_id", "enriched", "usdc_amount"):
        assert name in cols
    tables = {
        r[0]
        for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "erc20_transfers" in tables
    assert "address_labels" in tables
    assert "rebalance_events" in tables
    store.close()


def test_insert_enriched_rfq_and_transfer(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "t.db")
    fill = FluxionRfqFillTick(
        block_number=10,
        block_ts=1_700_000_000,
        recv_ts_ms=1_700_000_000_100,
        tx_hash="0x" + "ab" * 32,
        log_index=0,
        order_hash="0x" + "cd" * 32,
        remaining_making_amount=0,
        pair_id="SPCXx",
        maker=_addr(1),
        taker=_addr(2),
        direction="sell_native",
        making_token=_addr(3),
        taking_token=_addr(4),
        making_amount="1.5",
        taking_amount="150",
        usdc_amount="150",
        stock_amount="1.5",
        enriched=True,
    )
    xfer = Erc20TransferTick(
        pair_id="SPCXx",
        token=_addr(3),
        block_number=11,
        block_ts=1_700_000_002,
        recv_ts_ms=1_700_000_002_100,
        tx_hash="0x" + "ef" * 32,
        log_index=1,
        frm=_addr(1),
        to_addr=_addr(9),
        amount=Decimal("2"),
        amount_raw=2 * 10**18,
    )
    assert store.insert_rfq_fills([fill]) == 1
    assert store.insert_erc20_transfers([xfer]) == 1
    row = store._conn.execute(
        "SELECT maker, pair_id, enriched FROM fluxion_rfq_fills"
    ).fetchone()
    assert row["maker"] == _addr(1)
    assert row["pair_id"] == "SPCXx"
    assert row["enriched"] == 1
    assert store.count("erc20_transfers") == 1
    store.close()


def test_migrate_old_rfq_table_adds_columns(tmp_path: Path) -> None:
    """Pre-v4 DBs only had order_hash columns — migration ALTERs in place."""
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE fluxion_rfq_fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_number INTEGER NOT NULL,
            block_ts INTEGER NOT NULL,
            recv_ts_ms INTEGER NOT NULL,
            tx_hash TEXT NOT NULL,
            log_index INTEGER NOT NULL,
            order_hash TEXT NOT NULL,
            remaining_making_amount TEXT NOT NULL,
            gap INTEGER NOT NULL DEFAULT 0,
            UNIQUE (tx_hash, log_index)
        );
        INSERT INTO fluxion_rfq_fills (
            block_number, block_ts, recv_ts_ms, tx_hash, log_index,
            order_hash, remaining_making_amount, gap
        ) VALUES (1, 2, 3, '0xaa', 0, '0xbb', '0', 0);
        """
    )
    conn.commit()
    conn.close()

    store = SqliteStore(db)
    cols = {
        r[1]
        for r in store._conn.execute("PRAGMA table_info(fluxion_rfq_fills)").fetchall()
    }
    assert "maker" in cols
    assert "enriched" in cols
    unenriched = store.unenriched_rfq_fills()
    assert len(unenriched) == 1
    assert unenriched[0]["tx_hash"] == "0xaa"
    store.close()


def test_decode_erc20_transfer() -> None:
    token = _addr(5)
    frm = _addr(1)
    to = _addr(2)
    amount_raw = 10**18
    log = {
        "address": token,
        "topics": [
            TOPIC_TRANSFER,
            "0x" + "0" * 24 + frm[2:],
            "0x" + "0" * 24 + to[2:],
        ],
        "data": "0x" + f"{amount_raw:064x}",
        "blockNumber": hex(100),
        "logIndex": hex(3),
        "transactionHash": "0x" + "11" * 32,
    }
    tick = decode_erc20_transfer_log(
        log, pair_id="TSLAx", token=token, block_ts=1_700, recv_ts_ms=1
    )
    assert tick is not None
    assert tick.frm == frm
    assert tick.to_addr == to
    assert tick.amount == Decimal(1)
    assert tick.pair_id == "TSLAx"


def test_assign_market_maker_from_config_thresholds() -> None:
    cfg = load_attribution_config()
    feats = DraftAddressFeatures(
        address=_addr(1),
        n_pairs=1,
        n_amm=0,
        n_rfq_maker=5,
        n_rfq_taker=0,
        n_transfer=0,
        n_buy=0,
        n_sell=0,
        notional_usd=Decimal(1000),
        median_notional_usd=Decimal(100),
        max_notional_usd=Decimal(200),
        open_share=0.5,
        closed_share=0.5,
        convergence_ratio=None,
        n_convergence_scored=0,
        bybit_align_ratio=None,
        n_bybit_align_scored=0,
        both_directions=False,
        pairs=("SPCXx",),
        inv_mean_reversion=None,
    )
    result = assign_address_label(feats, cfg, cex_touch_transfers=0)
    assert result.label is BehaviorLabel.MARKET_MAKER
    assert result.source == "auto"


def test_manual_override_wins() -> None:
    cfg = load_attribution_config()
    feats = DraftAddressFeatures(
        address=_addr(7),
        n_pairs=1,
        n_amm=0,
        n_rfq_maker=0,
        n_rfq_taker=0,
        n_transfer=0,
        n_buy=0,
        n_sell=0,
        notional_usd=Decimal(0),
        median_notional_usd=Decimal(0),
        max_notional_usd=Decimal(0),
        open_share=None,
        closed_share=None,
        convergence_ratio=None,
        n_convergence_scored=0,
        bybit_align_ratio=None,
        n_bybit_align_scored=0,
        both_directions=False,
        pairs=(),
        inv_mean_reversion=None,
    )
    result = assign_address_label(
        feats,
        cfg,
        overrides={_addr(7): "market_maker"},
    )
    assert result.label is BehaviorLabel.MARKET_MAKER
    assert result.source == "manual"


def test_label_known_mm_case_from_rfq_fills() -> None:
    """Research candidate 0x1baab0b2… with ≥2 RFQ maker fills → market_maker."""
    cfg = load_attribution_config()
    maker = "0x1baab0b2e787eff5b3753addf31a1bcdc2417f9b"
    fills = [
        FluxionRfqFillTick(
            block_number=i,
            block_ts=1_700_000_000 + i,
            recv_ts_ms=1_700_000_000_000 + i,
            tx_hash="0x" + f"{i:064x}",
            log_index=0,
            order_hash="0x" + f"{i:064x}",
            remaining_making_amount=0,
            pair_id="SPCXx",
            maker=maker,
            taker=_addr(9),
            direction="sell_native",
            usdc_amount="100",
            stock_amount="1",
            enriched=True,
        )
        for i in range(3)
    ]
    results = label_addresses_from_journal(rfq_fills=fills, config=cfg)
    by_addr = {r.address: r for r in results}
    assert maker in by_addr
    assert by_addr[maker].label is BehaviorLabel.MARKET_MAKER


def test_rebalance_events_and_persist(tmp_path: Path) -> None:
    cfg = load_attribution_config()
    cex = cfg.rebalancer.cex_wallets[0]
    user = _addr(8)
    transfers = [
        Erc20TransferTick(
            pair_id="SPCXx",
            token=_addr(3),
            block_number=1,
            block_ts=1_700_000_000,
            recv_ts_ms=1,
            tx_hash="0x" + "aa" * 32,
            log_index=0,
            frm=user,
            to_addr=cex,
            amount=Decimal("5"),
            amount_raw=5 * 10**18,
        ),
        Erc20TransferTick(
            pair_id="SPCXx",
            token=_addr(3),
            block_number=2,
            block_ts=1_700_000_100,
            recv_ts_ms=2,
            tx_hash="0x" + "bb" * 32,
            log_index=0,
            frm=user,
            to_addr=cex,
            amount=Decimal("6"),
            amount_raw=6 * 10**18,
        ),
        Erc20TransferTick(
            pair_id="SPCXx",
            token=_addr(3),
            block_number=3,
            block_ts=1_700_000_200,
            recv_ts_ms=3,
            tx_hash="0x" + "cc" * 32,
            log_index=0,
            frm=user,
            to_addr=cex,
            amount=Decimal("7"),
            amount_raw=7 * 10**18,
        ),
    ]
    events = rebalance_events_from_transfers(
        transfers, cfg.rebalancer.cex_wallets, min_amount=Decimal("1")
    )
    assert len(events) == 3
    assert all(e.direction == "deposit_to_cex" for e in events)

    results = label_addresses_from_journal(transfers=transfers, config=cfg)
    by_addr = {r.address: r for r in results}
    assert by_addr[user].label is BehaviorLabel.REBALANCER
    assert by_addr[user].is_rebalancer is True

    store = SqliteStore(tmp_path / "reb.db")
    from monitor.attribution.address_labels import (
        persist_address_labels,
        persist_rebalance_events,
    )

    assert persist_rebalance_events(store, events) == 3
    assert persist_address_labels(store, results, updated_at_ms=123) == len(results)
    row = store._conn.execute(
        "SELECT label, is_rebalancer FROM address_labels WHERE address = ?",
        (user,),
    ).fetchone()
    assert row["label"] == "rebalancer"
    assert row["is_rebalancer"] == 1
    # Manual override sticky
    store.upsert_manual_address_label(
        address=user,
        label="market_maker",
        evidence_summary="desk",
        updated_at_ms=999,
    )
    # Auto refresh must not clobber
    store.upsert_address_label(
        address=user,
        label="retail",
        evidence_summary="auto",
        first_seen_ms=1,
        last_seen_ms=2,
        source="auto",
        updated_at_ms=1000,
    )
    row2 = store._conn.execute(
        "SELECT label, source FROM address_labels WHERE address = ?", (user,)
    ).fetchone()
    assert row2["label"] == "market_maker"
    assert row2["source"] == "manual"
    store.close()


def test_enrich_fill_from_receipt_pure() -> None:
    usdc = "0x09bc4e0d864854c6afb6eb9a9cdf58ac190d0df9"
    lop = "0x11de6011345586785810e52448a44c6595eedc18"
    maker = _addr(1)
    stock = _addr(3)
    row = {
        "block_number": 1,
        "block_ts": 2,
        "recv_ts_ms": 3,
        "tx_hash": "0x" + "dd" * 32,
        "log_index": 0,
        "order_hash": "0x" + "ee" * 32,
        "remaining_making_amount": "0",
        "gap": 0,
    }
    logs = [
        {
            "address": stock,
            "topics": [
                TOPIC_APPROVAL,
                "0x" + "0" * 24 + maker[2:],
                "0x" + "0" * 24 + lop[2:],
            ],
            "data": "0x" + "f" * 64,
        },
        {
            "address": stock,
            "topics": [
                TOPIC_TRANSFER,
                "0x" + "0" * 24 + maker[2:],
                "0x" + "0" * 24 + _addr(9)[2:],
            ],
            "data": "0x" + f"{10**18:064x}",
        },
        {
            "address": usdc,
            "topics": [
                TOPIC_TRANSFER,
                "0x" + "0" * 24 + _addr(9)[2:],
                "0x" + "0" * 24 + maker[2:],
            ],
            "data": "0x" + f"{150_000_000:064x}",
        },
    ]
    tick = enrich_fill_from_receipt(
        row=row,
        logs=logs,
        usdc=usdc,
        lop=lop,
        token_to_pair={stock: "SPCXx"},
    )
    assert tick.enriched is True
    assert tick.maker == maker
    assert tick.pair_id == "SPCXx"
    assert tick.direction == "sell_native"


def test_token_maps_cover_inventory_pairs() -> None:
    pairs = load_pairs_config()
    natives = native_token_to_pair(pairs)
    inv = inventory_token_to_pair(pairs)
    assert len(natives) == len(pairs.pairs)
    assert len(inv) == 2 * len(pairs.pairs)
    # Every native is also in inventory map
    for token, pid in natives.items():
        assert inv[token] == pid
