"""Unit tests for WHI-767 draft MM attribution pure helpers."""

from __future__ import annotations

from decimal import Decimal

from monitor.attribution.mm_draft import (
    DraftLabel,
    DraftThresholds,
    InventoryEvent,
    LedgerKind,
    TransferEdge,
    aggregate_address_features,
    assign_draft_label,
    build_position_series,
    cluster_cex_candidates,
    compute_pair_features,
    count_cex_touches,
    decode_rfq_fill_from_receipt,
    inventory_mean_reversion,
)


def test_inventory_mean_reversion_oscillation() -> None:
    # +1, 0, +1, 0 → two of three steps move toward zero
    series = [Decimal(1), Decimal(0), Decimal(1), Decimal(0)]
    ratio = inventory_mean_reversion(series)
    assert ratio is not None
    assert ratio == 2 / 3


def test_inventory_mean_reversion_pure_trend() -> None:
    series = [Decimal(1), Decimal(2), Decimal(3), Decimal(4)]
    assert inventory_mean_reversion(series) == 0.0


def test_build_position_series_cumulative() -> None:
    ev = [
        InventoryEvent(
            ts_ms=2,
            pair_id="TSLAx",
            address="0xAa",
            delta_native=Decimal("-3"),
            kind=LedgerKind.AMM_SWAP,
            tx_hash="0x2",
            direction="sell_native",
        ),
        InventoryEvent(
            ts_ms=1,
            pair_id="TSLAx",
            address="0xAa",
            delta_native=Decimal("5"),
            kind=LedgerKind.AMM_SWAP,
            tx_hash="0x1",
            direction="buy_native",
        ),
    ]
    series = build_position_series(ev, address="0xaa", pair_id="TSLAx")
    assert series.inventory == (Decimal(5), Decimal(2))
    assert series.final_inventory == Decimal(2)


def test_assign_market_maker_via_rfq_maker() -> None:
    from monitor.attribution.mm_draft import AddressFeatures

    feats = AddressFeatures(
        address="0xmm",
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
        both_directions=False,
        pairs=("TSLAx",),
        inv_mean_reversion=None,
    )
    labeled = assign_draft_label(feats, thresholds=DraftThresholds(mm_min_rfq_maker_fills=3))
    assert labeled.label is DraftLabel.MARKET_MAKER
    assert any("rfq_maker" in r for r in labeled.reasons)


def test_assign_arb_bot_via_convergence() -> None:
    from monitor.attribution.mm_draft import AddressFeatures

    feats = AddressFeatures(
        address="0xarb",
        n_pairs=1,
        n_amm=30,
        n_rfq_maker=0,
        n_rfq_taker=0,
        n_transfer=0,
        n_buy=20,
        n_sell=10,
        notional_usd=Decimal(50_000),
        median_notional_usd=Decimal(1500),
        max_notional_usd=Decimal(5000),
        open_share=0.9,
        closed_share=0.1,
        convergence_ratio=0.9,
        n_convergence_scored=25,
        both_directions=True,
        pairs=("TSLAx",),
        inv_mean_reversion=0.3,
    )
    labeled = assign_draft_label(feats)
    assert labeled.label is DraftLabel.ARB_BOT


def test_assign_rebalancer_via_cex_touches() -> None:
    from monitor.attribution.mm_draft import AddressFeatures

    feats = AddressFeatures(
        address="0xreb",
        n_pairs=1,
        n_amm=0,
        n_rfq_maker=0,
        n_rfq_taker=0,
        n_transfer=4,
        n_buy=0,
        n_sell=0,
        notional_usd=Decimal(0),
        median_notional_usd=Decimal(0),
        max_notional_usd=Decimal(0),
        open_share=None,
        closed_share=None,
        convergence_ratio=None,
        n_convergence_scored=0,
        both_directions=False,
        pairs=("TSLAx",),
        inv_mean_reversion=None,
    )
    labeled = assign_draft_label(feats, cex_touch_transfers=3)
    assert labeled.label is DraftLabel.REBALANCER


def test_arb_bot_outranks_rebalancer() -> None:
    from monitor.attribution.mm_draft import AddressFeatures

    feats = AddressFeatures(
        address="0xarb2",
        n_pairs=2,
        n_amm=30,
        n_rfq_maker=0,
        n_rfq_taker=0,
        n_transfer=10,
        n_buy=15,
        n_sell=15,
        notional_usd=Decimal(10_000),
        median_notional_usd=Decimal(300),
        max_notional_usd=Decimal(800),
        open_share=0.8,
        closed_share=0.2,
        convergence_ratio=0.9,
        n_convergence_scored=25,
        both_directions=True,
        pairs=("TSLAx", "AAPLx"),
        inv_mean_reversion=0.4,
    )
    labeled = assign_draft_label(feats, cex_touch_transfers=50)
    assert labeled.label is DraftLabel.ARB_BOT


def test_decode_rfq_fill_maker_from_approval() -> None:
    maker = "0x" + "11" * 20
    taker = "0x" + "22" * 20
    router = "0x41dee1855293e4450cd67459047f372d4d818143"
    usdc = "0x09bc4e0d864854c6afb6eb9a9cdf58ac190d0df9"
    lop = "0x11de6011345586785810e52448a44c6595eedc18"
    stock = "0x68fa48b1c2fe52b3d776e1953e0e782b5044ce28"  # SPCXx native
    # Approval(maker, LOP, amount)
    approval = {
        "address": stock,
        "topics": [
            "0x" + __import__("eth_utils").keccak(text="Approval(address,address,uint256)").hex(),
            "0x" + "0" * 24 + maker[2:],
            "0x" + "0" * 24 + lop[2:],
        ],
        "data": "0x" + (10**18).to_bytes(32, "big").hex(),
    }
    # stock maker → router
    xfer_stock = {
        "address": stock,
        "topics": [
            "0x" + __import__("eth_utils").keccak(text="Transfer(address,address,uint256)").hex(),
            "0x" + "0" * 24 + maker[2:],
            "0x" + "0" * 24 + router[2:],
        ],
        "data": "0x" + (5 * 10**18).to_bytes(32, "big").hex(),
    }
    # usdc router → taker
    xfer_usdc = {
        "address": usdc,
        "topics": [
            "0x" + __import__("eth_utils").keccak(text="Transfer(address,address,uint256)").hex(),
            "0x" + "0" * 24 + router[2:],
            "0x" + "0" * 24 + taker[2:],
        ],
        "data": "0x" + (100 * 10**6).to_bytes(32, "big").hex(),
    }
    decoded = decode_rfq_fill_from_receipt(
        tx_hash="0xabc",
        logs=[approval, xfer_stock, xfer_usdc],
        usdc=usdc,
        lop=lop,
        settlement_router=router,
        token_to_pair={stock: "SPCXx"},
    )
    assert decoded.maker == maker
    assert decoded.taker == taker
    assert decoded.pair_id == "SPCXx"
    assert decoded.maker_side == "sell_native"


def test_cluster_cex_and_count_touches() -> None:
    cex = "0x" + "ce" * 20
    users = ["0x" + f"{i:02x}" * 20 for i in range(1, 8)]
    transfers = [
        TransferEdge(
            ts_ms=i,
            pair_id="TSLAx",
            token="0xt",
            frm=u,
            to=cex,
            amount=Decimal(10),
            tx_hash=f"0x{i}",
        )
        for i, u in enumerate(users)
    ]
    clusters = cluster_cex_candidates(
        transfers, min_counterparties=5, min_transfers=5
    )
    assert clusters
    assert clusters[0].address == cex
    assert count_cex_touches(transfers, users[0], [cex]) == 1


def test_compute_pair_features_directions() -> None:
    ev = [
        InventoryEvent(
            ts_ms=1,
            pair_id="AAPLx",
            address="0x1",
            delta_native=Decimal("1"),
            kind=LedgerKind.AMM_SWAP,
            tx_hash="0x1",
            direction="buy_native",
            notional_usd=Decimal(100),
            converges=True,
            session="open",
        ),
        InventoryEvent(
            ts_ms=2,
            pair_id="AAPLx",
            address="0x1",
            delta_native=Decimal("-1"),
            kind=LedgerKind.AMM_SWAP,
            tx_hash="0x2",
            direction="sell_native",
            notional_usd=Decimal(120),
            converges=False,
            session="closed",
        ),
    ]
    series = build_position_series(ev, address="0x1", pair_id="AAPLx")
    feats = compute_pair_features(series)
    assert feats.n_buy == 1 and feats.n_sell == 1
    assert feats.convergence_ratio == 0.5
    assert feats.n_amm == 2
    agg = aggregate_address_features([feats], address="0x1")
    assert agg.both_directions
    assert agg.n_pairs == 1
