"""GET /api/pairs and pair detail / trades / mm (WHI-769)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from monitor.api.serialize import to_json_dict, to_jsonable
from monitor.api.state import AppState, app_state_from_request
from monitor.attribution.address_labels import inventory_events_from_ticks
from monitor.attribution.mm_draft import InventoryEvent
from monitor.attribution.mm_panel import (
    MM_LABEL,
    MmActiveStatus,
    build_address_panel_rows,
    build_mm_pair_snapshot,
    labels_by_address,
    mm_active_status,
    pair_active_addresses,
)
from monitor.metrics.pnl_snapshot import (
    PnlOptimalSummary,
    PnlPairSnapshot,
    build_pnl_pair_snapshot,
    overview_pnl_summary,
)
from monitor.quotes import now_ms
from monitor.storage import JournalReader
from monitor.storage.reader import AddressLabelRow
from monitor.symbols.models import Pair
from monitor.symbols.token_map import quote_is_token0_by_pair
from monitor.tui.builder import build_overview, build_pair_detail
from monitor.tui.model import PairDetailModel
from monitor.tui.pool import amm_pool_from_tick

router = APIRouter(tags=["pairs"])


def _require_reader(state: AppState) -> JournalReader:
    reader = state.ensure_reader()
    if reader is None:
        raise HTTPException(
            status_code=503,
            detail=f"collector journal not found: {state.db_path}",
        )
    return reader


def _pair_or_404(state: AppState, pair_id: str) -> Pair:
    try:
        return state.pairs.pair_by_id(pair_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown pair_id: {pair_id}") from exc


def _pnl_snapshot_for_pair(
    state: AppState,
    *,
    pair: Pair,
    reader: JournalReader,
) -> PnlPairSnapshot:
    """Load ticks + build PnL snapshot, with optional TTL cache (caller holds lock)."""
    cache = state.pnl_cache
    if cache is not None:
        hit = cache.get(pair.id)
        if hit is not None:
            return hit

    bybit = reader.latest_bybit_book(pair.id)
    amm_tick = reader.latest_pool_state(pair.id)
    depth = reader.latest_bybit_depth(pair.id)
    rfq_buy, rfq_sell = reader.latest_rfq_sides(pair.id)
    amm = amm_pool_from_tick(pair, amm_tick) if amm_tick is not None else None
    snap = build_pnl_pair_snapshot(
        pair_id=pair.id,
        bybit=bybit,
        amm=amm,
        amm_tick=amm_tick,
        config=state.metrics,
        depth=depth,
        rfq_buy=rfq_buy,
        rfq_sell=rfq_sell,
        native_decimals=pair.fluxion.native_decimals,
        rfq_enabled=True,
        now_ms=now_ms(),
        stale_ms=state.api.collector_stale_ms,
    )
    if cache is not None:
        cache.put(pair.id, snap)
    return snap


def _inventory_events(
    state: AppState, reader: JournalReader
) -> list[InventoryEvent]:
    """Ledger events for MM activity / inventory curves (caller holds lock)."""
    cache = state.inventory_cache
    if cache is not None:
        hit = cache.get()
        if hit is not None:
            return hit
    q0 = quote_is_token0_by_pair(state.pairs)
    events = inventory_events_from_ticks(
        swaps=reader.recent_swaps(),
        rfq_fills=reader.recent_rfq_fills(),
        transfers=reader.recent_erc20_transfers(),
        quote_is_token0_by_pair=q0,
    )
    if cache is not None:
        cache.put(events)
    return events


def _mm_active_for(
    state: AppState,
    *,
    pair_id: str,
    label_count: int,
    mm_labels: list[AddressLabelRow],
    inv_events: list[InventoryEvent],
    now: int,
) -> MmActiveStatus:
    """Shared overview badge path for list + detail (caller holds lock)."""
    return mm_active_status(
        label_count=label_count,
        mm_labels=mm_labels,
        inventory_events=inv_events,
        pair_id=pair_id,
        now_ms=now,
        window_ms=state.api.mm_active_window_ms,
    )


def _detail_model(state: AppState, pair_id: str) -> tuple[PairDetailModel, PnlPairSnapshot]:
    """Build detail + PnL under the process lock (shared by detail + trades)."""
    reader = _require_reader(state)
    pair = _pair_or_404(state, pair_id)
    with state.lock:
        model = build_pair_detail(
            pair=pair,
            reader=reader,
            metrics=state.metrics,
            attribution_cfg=state.attribution,
            tui=state.tui,
            edge_state=state.edge_state,
        )
        pnl = _pnl_snapshot_for_pair(state, pair=pair, reader=reader)
        return model, pnl


@router.get("/api/pairs")
def list_pairs(request: Request) -> dict[str, Any]:
    """Overview table + PnL v2 optimal summary + MM active badge (WHI-769)."""
    state = app_state_from_request(request)
    reader = _require_reader(state)
    with state.lock:
        model = build_overview(
            pairs=state.pairs,
            reader=reader,
            metrics=state.metrics,
            tui=state.tui,
            edge_state=state.edge_state,
        )
        body = to_json_dict(model)
        label_count = reader.address_label_count()
        mm_labels = (
            reader.address_labels(label=MM_LABEL) if label_count > 0 else []
        )
        inv_events = (
            _inventory_events(state, reader)
            if label_count > 0 and mm_labels
            else []
        )
        ts = now_ms()
        rows_out: list[dict[str, Any]] = []
        for row in body["rows"]:
            pair_id = row["pair_id"]
            try:
                pair = state.pairs.pair_by_id(pair_id)
            except KeyError:
                # Builder rows should always be configured pairs; never 404 the list.
                enriched = dict(row)
                enriched["pnl_v2"] = PnlOptimalSummary(
                    status="no_pool", has_depth=False
                ).to_dict()
                enriched["mm_active"] = "unknown"
                rows_out.append(enriched)
                continue
            snap = _pnl_snapshot_for_pair(state, pair=pair, reader=reader)
            enriched = dict(row)
            enriched["pnl_v2"] = overview_pnl_summary(snap).to_dict()
            enriched["mm_active"] = _mm_active_for(
                state,
                pair_id=pair_id,
                label_count=label_count,
                mm_labels=mm_labels,
                inv_events=inv_events,
                now=ts,
            )
            rows_out.append(enriched)
        body["rows"] = rows_out
        return body


@router.get("/api/pairs/{pair_id}")
def get_pair(pair_id: str, request: Request) -> dict[str, Any]:
    """Detail model + PnL v2 + extended attribution address panel (WHI-769)."""
    state = app_state_from_request(request)
    reader = _require_reader(state)
    model, pnl = _detail_model(state, pair_id)
    body = to_json_dict(model)
    body["pnl_v2"] = pnl.to_dict()

    with state.lock:
        label_count = reader.address_label_count()
        all_labels = reader.address_labels() if label_count > 0 else []
        mm_labels = [r for r in all_labels if r.label == MM_LABEL]
        inv = (
            _inventory_events(state, reader)
            if label_count > 0 and mm_labels
            else []
        )
        if "overview" in body and isinstance(body["overview"], dict):
            body["overview"] = dict(body["overview"])
            body["overview"]["pnl_v2"] = overview_pnl_summary(pnl).to_dict()
            body["overview"]["mm_active"] = _mm_active_for(
                state,
                pair_id=pair_id,
                label_count=label_count,
                mm_labels=mm_labels,
                inv_events=inv,
                now=now_ms(),
            )
        labels = labels_by_address(all_labels)
        active = pair_active_addresses(inv, pair_id) if inv else set()
        # Also mark addresses that appear as AMM top takers so they stay in panel.
        if model.attribution is not None:
            for t in model.attribution.top_takers:
                active.add(t.address.lower())
        panel_rows = build_address_panel_rows(
            attribution=model.attribution,
            labels=labels,
            top_n=state.attribution.top_takers_n,
            pair_active=active if label_count > 0 else None,
        )
    body["address_panel"] = [r.to_dict() for r in panel_rows]
    return body


@router.get("/api/pairs/{pair_id}/mm")
def get_pair_mm(pair_id: str, request: Request) -> dict[str, Any]:
    """MM inventory curves + rebalance timeline for one pair (WHI-769)."""
    state = app_state_from_request(request)
    reader = _require_reader(state)
    _pair_or_404(state, pair_id)
    with state.lock:
        labels = reader.address_labels()
        inv = _inventory_events(state, reader)
        reb = reader.rebalance_events(
            pair_id=pair_id, limit=state.api.mm_rebalance_limit
        )
        snap = build_mm_pair_snapshot(
            pair_id=pair_id,
            labels=labels,
            inventory_events=inv,
            rebalance_events=reb,
            generated_ts_ms=now_ms(),
            max_series_points=state.api.mm_series_max_points,
            max_rebalance=state.api.mm_rebalance_limit,
        )
        return snap.to_dict()


@router.get("/api/pairs/{pair_id}/trades")
def get_pair_trades(pair_id: str, request: Request) -> dict[str, Any]:
    """Trade stream for one pair (subset of the detail model).

    Skeleton reuses ``build_pair_detail`` so trade labels stay identical to the
    detail page; a cheaper path can land when poll load requires it.
    """
    state = app_state_from_request(request)
    model, _pnl = _detail_model(state, pair_id)
    return {
        "pair_id": model.pair_id,
        "generated_ts_ms": model.generated_ts_ms,
        "trades": to_jsonable(model.trades),
    }
