"""WHI-769: pure MM panel builders (address rows, inventory, mm_active)."""

from __future__ import annotations

from decimal import Decimal

from monitor.attribution.aggregate import MechanismShare, PairAttribution
from monitor.attribution.labels import (
    ActivityRegime,
    AddressFeatures,
    BehaviorLabel,
    TakerProfile,
)
from monitor.attribution.mm_draft import InventoryEvent, LedgerKind
from monitor.attribution.mm_panel import (
    build_address_panel_rows,
    build_mm_pair_snapshot,
    labels_by_address,
    mm_active_status,
)
from monitor.storage.reader import AddressLabelRow, RebalanceEventRow


def _addr(n: int) -> str:
    return "0x" + f"{n:040x}"


def _label(
    address: str,
    *,
    label: str = "market_maker",
    evidence: str = "rfq_maker_fills",
    last_seen_ms: int | None = 1_700_000_100_000,
    n_rfq: int = 5,
    n_amm: int = 0,
    is_rebalancer: bool = False,
) -> AddressLabelRow:
    return AddressLabelRow(
        address=address.lower(),
        label=label,
        evidence_summary=evidence,
        first_seen_ms=1_700_000_000_000,
        last_seen_ms=last_seen_ms,
        source="auto",
        is_rebalancer=is_rebalancer,
        n_rfq_maker=n_rfq,
        n_amm=n_amm,
        cex_touch_transfers=2 if is_rebalancer else 0,
        updated_at_ms=1_700_000_200_000,
    )


def _taker(
    address: str,
    *,
    label: BehaviorLabel = BehaviorLabel.ARB_BOT,
    n_trades: int = 20,
    notional: str = "5000",
    conv: float | None = 0.8,
) -> TakerProfile:
    feats = AddressFeatures(
        address=address.lower(),
        n_trades=n_trades,
        n_buy=n_trades // 2,
        n_sell=n_trades - n_trades // 2,
        notional_usd=Decimal(notional),
        median_notional_usd=Decimal("100"),
        max_notional_usd=Decimal("200"),
        open_share=0.6,
        closed_share=0.4,
        activity_regime=ActivityRegime.ALL_HOURS,
        trades_per_day=10.0,
        convergence_ratio=conv,
        n_convergence_scored=n_trades,
        bybit_align_ratio=0.7,
        n_bybit_align_scored=n_trades,
        is_contract=True,
        role=None,
    )
    return TakerProfile(features=feats, label=label)


def _attr(takers: list[TakerProfile]) -> PairAttribution:
    return PairAttribution(
        pair_id="SPCXx",
        session="all",
        window_start_ms=1,
        window_end_ms=2,
        mechanism=MechanismShare(amm_trades=10, rfq_trades=2),
        convergence_share=0.5,
        n_convergence_scored=10,
        label_trade_share={lab: 0.0 for lab in BehaviorLabel},
        label_trade_counts={lab: 0 for lab in BehaviorLabel},
        top_takers=takers,
        takers=takers,
    )


def test_address_panel_merges_label_evidence_and_ranks_mm_first() -> None:
    mm = _addr(1)
    arb = _addr(2)
    labels = labels_by_address(
        [
            _label(mm, evidence="n_rfq_maker>=2"),
            _label(arb, label="arb_bot", evidence="conv_high"),
        ]
    )
    attr = _attr(
        [
            _taker(arb, label=BehaviorLabel.ARB_BOT, n_trades=50),
            _taker(mm, label=BehaviorLabel.UNKNOWN, n_trades=3),
        ]
    )
    rows = build_address_panel_rows(attribution=attr, labels=labels, top_n=10)
    assert rows[0].address == mm
    assert rows[0].label == "market_maker"
    assert rows[0].evidence_summary == "n_rfq_maker>=2"
    assert rows[0].is_rebalancer is False
    assert rows[1].label == "arb_bot"
    assert rows[1].evidence_summary == "conv_high"


def test_address_panel_injects_rfq_only_mm() -> None:
    mm = _addr(9)
    labels = labels_by_address([_label(mm, n_rfq=4, n_amm=0)])
    # No AMM takers at all — MM still surfaces from labels.
    attr = _attr([])
    rows = build_address_panel_rows(attribution=attr, labels=labels, top_n=5)
    assert len(rows) == 1
    assert rows[0].address == mm
    assert rows[0].n_rfq_maker == 4


def test_mm_active_three_states() -> None:
    mm = _addr(1)
    now = 1_700_100_000_000
    labels = [_label(mm, last_seen_ms=now)]
    events = [
        InventoryEvent(
            ts_ms=now - 60_000,
            pair_id="SPCXx",
            address=mm,
            delta_native=Decimal("1"),
            kind=LedgerKind.RFQ_FILL,
            tx_hash="0x" + "ab" * 32,
            role="maker",
        )
    ]
    assert (
        mm_active_status(
            label_count=0,
            mm_labels=[],
            inventory_events=[],
            pair_id="SPCXx",
            now_ms=now,
        )
        == "unknown"
    )
    assert (
        mm_active_status(
            label_count=5,
            mm_labels=labels,
            inventory_events=events,
            pair_id="SPCXx",
            now_ms=now,
        )
        == "active"
    )
    assert (
        mm_active_status(
            label_count=5,
            mm_labels=labels,
            inventory_events=events,
            pair_id="AAPLx",
            now_ms=now,
        )
        == "inactive"
    )
    # Stale activity outside 24h window.
    old = [
        InventoryEvent(
            ts_ms=now - 2 * 24 * 60 * 60 * 1000,
            pair_id="SPCXx",
            address=mm,
            delta_native=Decimal("1"),
            kind=LedgerKind.AMM_SWAP,
            tx_hash="0x" + "cd" * 32,
        )
    ]
    assert (
        mm_active_status(
            label_count=5,
            mm_labels=labels,
            inventory_events=old,
            pair_id="SPCXx",
            now_ms=now,
        )
        == "inactive"
    )


def test_mm_pair_snapshot_accumulating_and_ok() -> None:
    mm = _addr(1)
    now = 1_700_100_000_000
    empty = build_mm_pair_snapshot(
        pair_id="SPCXx",
        labels=[],
        inventory_events=[],
        rebalance_events=[],
        generated_ts_ms=now,
    )
    assert empty.status == "accumulating"
    assert empty.addresses == []

    events = [
        InventoryEvent(
            ts_ms=now - 10_000,
            pair_id="SPCXx",
            address=mm,
            delta_native=Decimal("2"),
            kind=LedgerKind.RFQ_FILL,
            tx_hash="0x" + "11" * 32,
            role="maker",
        ),
        InventoryEvent(
            ts_ms=now - 5_000,
            pair_id="SPCXx",
            address=mm,
            delta_native=Decimal("-0.5"),
            kind=LedgerKind.RFQ_FILL,
            tx_hash="0x" + "22" * 32,
            role="maker",
        ),
    ]
    reb = [
        RebalanceEventRow(
            address=mm,
            counterparty=_addr(99),
            pair_id="SPCXx",
            token=_addr(50),
            amount=Decimal("10"),
            direction="deposit_to_cex",
            block_number=100,
            block_ts=now // 1000,
            recv_ts_ms=now,
            tx_hash="0x" + "33" * 32,
            log_index=0,
        )
    ]
    snap = build_mm_pair_snapshot(
        pair_id="SPCXx",
        labels=[_label(mm)],
        inventory_events=events,
        rebalance_events=reb,
        generated_ts_ms=now,
    )
    assert snap.status == "ok"
    assert len(snap.addresses) == 1
    series = snap.addresses[0]
    assert series.final_inventory == Decimal("1.5")
    assert len(series.series) == 2
    assert series.series[-1].inventory == Decimal("1.5")
    assert len(snap.rebalance_events) == 1
    assert snap.rebalance_events[0].direction == "deposit_to_cex"

    no_pair = build_mm_pair_snapshot(
        pair_id="AAPLx",
        labels=[_label(mm)],
        inventory_events=events,
        rebalance_events=[],
        generated_ts_ms=now,
    )
    assert no_pair.status == "no_candidates"
