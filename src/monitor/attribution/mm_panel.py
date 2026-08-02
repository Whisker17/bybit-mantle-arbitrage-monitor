"""MM panel view models for API + Web (WHI-769).

Pure assembly over address_labels, inventory ledger events, and rebalance
rows. Callers load ticks from ``JournalReader`` / pair attribution.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from monitor.attribution.aggregate import PairAttribution
from monitor.attribution.labels import BehaviorLabel, TakerProfile
from monitor.attribution.mm_draft import (
    InventoryEvent,
    build_position_series,
)
from monitor.storage.reader import AddressLabelRow, RebalanceEventRow

# Overview badge: MM activity on the pair in the lookback window.
MmActiveStatus = Literal["active", "inactive", "unknown"]

# Detail /mm empty-state machine (issue: 数据积累中 / 无候选 / ok).
MmDataStatus = Literal["ok", "accumulating", "no_candidates"]

_MM_LABEL = BehaviorLabel.MARKET_MAKER.value
_DEFAULT_ACTIVE_WINDOW_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True, slots=True)
class AddressPanelRow:
    """Top-address row for detail attribution (label + evidence + stats)."""

    address: str
    label: str
    evidence_summary: str | None
    is_rebalancer: bool
    n_trades: int
    notional_usd: Decimal
    convergence_ratio: float | None
    last_active_ms: int | None
    source: str | None  # auto | manual | None (live AMM-only, not yet persisted)
    n_rfq_maker: int
    n_amm: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "label": self.label,
            "evidence_summary": self.evidence_summary,
            "is_rebalancer": self.is_rebalancer,
            "n_trades": self.n_trades,
            "notional_usd": format(self.notional_usd, "f"),
            "convergence_ratio": self.convergence_ratio,
            "last_active_ms": self.last_active_ms,
            "source": self.source,
            "n_rfq_maker": self.n_rfq_maker,
            "n_amm": self.n_amm,
        }


@dataclass(frozen=True, slots=True)
class InventoryPoint:
    ts_ms: int
    inventory: Decimal
    tx_hash: str
    kind: str
    delta_native: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_ms": self.ts_ms,
            "inventory": format(self.inventory, "f"),
            "tx_hash": self.tx_hash,
            "kind": self.kind,
            "delta_native": format(self.delta_native, "f"),
        }


@dataclass(frozen=True, slots=True)
class MmAddressSeries:
    address: str
    label: str
    evidence_summary: str | None
    is_rebalancer: bool
    final_inventory: Decimal
    series: list[InventoryPoint]

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "label": self.label,
            "evidence_summary": self.evidence_summary,
            "is_rebalancer": self.is_rebalancer,
            "final_inventory": format(self.final_inventory, "f"),
            "series": [p.to_dict() for p in self.series],
        }


@dataclass(frozen=True, slots=True)
class RebalanceTimelineItem:
    address: str
    counterparty: str
    pair_id: str
    token: str
    amount: Decimal
    direction: str
    block_number: int
    block_ts: int
    recv_ts_ms: int
    tx_hash: str
    log_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "counterparty": self.counterparty,
            "pair_id": self.pair_id,
            "token": self.token,
            "amount": format(self.amount, "f"),
            "direction": self.direction,
            "block_number": self.block_number,
            "block_ts": self.block_ts,
            "recv_ts_ms": self.recv_ts_ms,
            "tx_hash": self.tx_hash,
            "log_index": self.log_index,
        }


@dataclass(frozen=True, slots=True)
class MmPairSnapshot:
    """GET /api/pairs/{id}/mm payload (inventory curves + rebalance timeline)."""

    pair_id: str
    status: MmDataStatus
    generated_ts_ms: int
    addresses: list[MmAddressSeries]
    rebalance_events: list[RebalanceTimelineItem]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "status": self.status,
            "generated_ts_ms": self.generated_ts_ms,
            "addresses": [a.to_dict() for a in self.addresses],
            "rebalance_events": [e.to_dict() for e in self.rebalance_events],
        }


def labels_by_address(
    rows: Sequence[AddressLabelRow],
) -> dict[str, AddressLabelRow]:
    return {r.address.lower(): r for r in rows}


def build_address_panel_rows(
    *,
    attribution: PairAttribution | None,
    labels: Mapping[str, AddressLabelRow],
    top_n: int = 10,
) -> list[AddressPanelRow]:
    """Merge live top takers with persisted full-address labels (MM path).

    Priority for ranking: market_maker first, then trade-count (existing
    top_takers order), then label-only MM addresses that never hit the AMM
    taker path (RFQ makers).
    """
    by_addr: dict[str, AddressPanelRow] = {}

    takers: Sequence[TakerProfile] = ()
    if attribution is not None:
        takers = attribution.top_takers or attribution.takers[:top_n]

    for t in takers:
        addr = t.address.lower()
        f = t.features
        stored = labels.get(addr)
        if stored is not None:
            label = stored.label
            evidence = stored.evidence_summary or None
            is_reb = stored.is_rebalancer
            source = stored.source
            last_ms = stored.last_seen_ms
            n_rfq = stored.n_rfq_maker
            n_amm = stored.n_amm
        else:
            label = t.label.value
            evidence = None
            is_reb = False
            source = None
            last_ms = None
            n_rfq = 0
            n_amm = f.n_trades
        by_addr[addr] = AddressPanelRow(
            address=addr,
            label=label,
            evidence_summary=evidence,
            is_rebalancer=is_reb,
            n_trades=f.n_trades,
            notional_usd=f.notional_usd,
            convergence_ratio=f.convergence_ratio,
            last_active_ms=last_ms,
            source=source,
            n_rfq_maker=n_rfq,
            n_amm=n_amm,
        )

    # Inject label-only market makers not present as AMM top takers (RFQ makers).
    for addr, stored in labels.items():
        if stored.label != _MM_LABEL:
            continue
        if addr in by_addr:
            continue
        by_addr[addr] = AddressPanelRow(
            address=addr,
            label=stored.label,
            evidence_summary=stored.evidence_summary or None,
            is_rebalancer=stored.is_rebalancer,
            n_trades=stored.n_amm + stored.n_rfq_maker,
            notional_usd=Decimal(0),
            convergence_ratio=None,
            last_active_ms=stored.last_seen_ms,
            source=stored.source,
            n_rfq_maker=stored.n_rfq_maker,
            n_amm=stored.n_amm,
        )

    rows = list(by_addr.values())

    def _sort_key(r: AddressPanelRow) -> tuple[int, int, str]:
        # market_maker first, then by n_trades desc, then address.
        mm_rank = 0 if r.label == _MM_LABEL else 1
        return (mm_rank, -r.n_trades, r.address)

    rows.sort(key=_sort_key)
    return rows[:top_n]


def mm_active_status(
    *,
    label_count: int,
    mm_labels: Sequence[AddressLabelRow],
    inventory_events: Sequence[InventoryEvent],
    pair_id: str,
    now_ms: int,
    window_ms: int = _DEFAULT_ACTIVE_WINDOW_MS,
) -> MmActiveStatus:
    """Overview three-state MM badge for one pair.

    - ``unknown``: no address_labels rows (refresh never ran / empty journal)
    - ``active``: a market_maker address has inventory events on this pair
      inside the lookback window
    - ``inactive``: labels exist but no recent MM activity on this pair
    """
    if label_count <= 0:
        return "unknown"
    mm_addrs = {r.address.lower() for r in mm_labels if r.label == _MM_LABEL}
    if not mm_addrs:
        # Labels refreshed but no MM candidates globally.
        return "inactive"
    cutoff = now_ms - window_ms
    for e in inventory_events:
        if e.pair_id != pair_id:
            continue
        if e.address.lower() not in mm_addrs:
            continue
        if e.ts_ms >= cutoff:
            return "active"
    # Also treat recent last_seen on an MM that traded this pair historically
    # via pair_id on stored features is unavailable — fall back to inactive.
    return "inactive"


def build_mm_pair_snapshot(
    *,
    pair_id: str,
    labels: Sequence[AddressLabelRow],
    inventory_events: Sequence[InventoryEvent],
    rebalance_events: Sequence[RebalanceEventRow],
    generated_ts_ms: int,
    max_series_points: int = 500,
    max_rebalance: int = 100,
) -> MmPairSnapshot:
    """Inventory curves for MM addresses + rebalance timeline for one pair."""
    if not labels:
        return MmPairSnapshot(
            pair_id=pair_id,
            status="accumulating",
            generated_ts_ms=generated_ts_ms,
            addresses=[],
            rebalance_events=[],
        )

    mm_rows = [r for r in labels if r.label == _MM_LABEL]
    # Prefer MMs that have inventory on this pair; still surface global MMs
    # with empty series so the UI can show the label + empty chart message.
    pair_events = [e for e in inventory_events if e.pair_id == pair_id]
    addrs_with_events = {e.address.lower() for e in pair_events}
    mm_for_pair = [
        r
        for r in mm_rows
        if r.address in addrs_with_events
        or r.n_amm > 0
        or r.n_rfq_maker > 0
    ]
    # If no MM labels at all → no candidates; if MM labels but none touch
    # this pair's events → still no_candidates for the pair panel.
    if not mm_rows:
        return MmPairSnapshot(
            pair_id=pair_id,
            status="no_candidates",
            generated_ts_ms=generated_ts_ms,
            addresses=[],
            rebalance_events=[
                _reb_item(e)
                for e in rebalance_events
                if e.pair_id == pair_id
            ][:max_rebalance],
        )

    # Restrict chart addresses to those with pair activity when available.
    chart_rows = [r for r in mm_for_pair if r.address in addrs_with_events]
    if not chart_rows:
        # Global MMs exist but none have inventory on this pair.
        return MmPairSnapshot(
            pair_id=pair_id,
            status="no_candidates",
            generated_ts_ms=generated_ts_ms,
            addresses=[],
            rebalance_events=[
                _reb_item(e)
                for e in rebalance_events
                if e.pair_id == pair_id
            ][:max_rebalance],
        )

    series_out: list[MmAddressSeries] = []
    for row in sorted(chart_rows, key=lambda r: r.address):
        pos = build_position_series(
            pair_events, address=row.address, pair_id=pair_id
        )
        points: list[InventoryPoint] = []
        # Keep last N points for chart density.
        start = max(0, len(pos.events) - max_series_points)
        for ev, inv in zip(
            pos.events[start:], pos.inventory[start:], strict=True
        ):
            points.append(
                InventoryPoint(
                    ts_ms=ev.ts_ms,
                    inventory=inv,
                    tx_hash=ev.tx_hash,
                    kind=str(ev.kind.value if hasattr(ev.kind, "value") else ev.kind),
                    delta_native=ev.delta_native,
                )
            )
        series_out.append(
            MmAddressSeries(
                address=row.address,
                label=row.label,
                evidence_summary=row.evidence_summary or None,
                is_rebalancer=row.is_rebalancer,
                final_inventory=pos.final_inventory,
                series=points,
            )
        )

    reb = [
        _reb_item(e)
        for e in rebalance_events
        if e.pair_id == pair_id
        or e.address.lower() in {r.address for r in chart_rows}
    ][:max_rebalance]

    return MmPairSnapshot(
        pair_id=pair_id,
        status="ok",
        generated_ts_ms=generated_ts_ms,
        addresses=series_out,
        rebalance_events=reb,
    )


def _reb_item(e: RebalanceEventRow) -> RebalanceTimelineItem:
    return RebalanceTimelineItem(
        address=e.address,
        counterparty=e.counterparty,
        pair_id=e.pair_id,
        token=e.token,
        amount=e.amount,
        direction=e.direction,
        block_number=e.block_number,
        block_ts=e.block_ts,
        recv_ts_ms=e.recv_ts_ms,
        tx_hash=e.tx_hash,
        log_index=e.log_index,
    )
