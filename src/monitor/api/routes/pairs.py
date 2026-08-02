"""GET /api/pairs and pair detail / trades / mm (WHI-769)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from monitor.api.serialize import to_json_dict, to_jsonable
from monitor.api.state import AppState, app_state_from_request
from monitor.attribution.address_labels import inventory_events_from_ticks
from monitor.attribution.mm_draft import InventoryEvent
from monitor.attribution.mm_panel import (
    build_address_panel_rows,
    build_mm_pair_snapshot,
    labels_by_address,
    mm_active_status,
)
from monitor.metrics.pnl_snapshot import (
    PnlOptimalSummary,
    PnlPairSnapshot,
    build_pnl_pair_snapshot,
    overview_pnl_summary,
)
from monitor.quotes import now_ms
from monitor.storage import JournalReader
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
        pair=pair,
        bybit=bybit,
        amm=amm,
        amm_tick=amm_tick,
        config=state.metrics,
        depth=depth,
        rfq_buy=rfq_buy,
        rfq_sell=rfq_sell,
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
    q0 = quote_is_token0_by_pair(state.pairs)
    return inventory_events_from_ticks(
        swaps=reader.recent_swaps(),
        rfq_fills=reader.recent_rfq_fills(),
        transfers=reader.recent_erc20_transfers(),
        quote_is_token0_by_pair=q0,
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
        all_labels = reader.address_labels() if label_count > 0 else []
        mm_labels = [r for r in all_labels if r.label == "market_maker"]
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
            enriched["mm_active"] = mm_active_status(
                label_count=label_count,
                mm_labels=mm_labels,
                inventory_events=inv_events,
                pair_id=pair_id,
                now_ms=ts,
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
    # Keep overview row in sync with the same snapshot (summary view).
    if "overview" in body and isinstance(body["overview"], dict):
        body["overview"] = dict(body["overview"])
        body["overview"]["pnl_v2"] = overview_pnl_summary(pnl).to_dict()
        with state.lock:
            label_count = reader.address_label_count()
            mm_labels = (
                reader.address_labels(label="market_maker")
                if label_count > 0
                else []
            )
            inv = (
                _inventory_events(state, reader)
                if label_count > 0 and mm_labels
                else []
            )
            body["overview"]["mm_active"] = mm_active_status(
                label_count=label_count,
                mm_labels=mm_labels,
                inventory_events=inv,
                pair_id=pair_id,
                now_ms=now_ms(),
            )

    # Extended top-address list (labels + evidence) for the attribution panel.
    with state.lock:
        labels = labels_by_address(reader.address_labels())
        top_n = state.attribution.top_takers_n
        panel_rows = build_address_panel_rows(
            attribution=model.attribution,
            labels=labels,
            top_n=top_n,
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
        reb = reader.rebalance_events(pair_id=pair_id, limit=200)
        # Also include rebalances for MM addresses that may be tagged other pairs.
        mm_addrs = [r.address for r in labels if r.label == "market_maker"]
        for addr in mm_addrs:
            extra = reader.rebalance_events(address=addr, limit=50)
            seen = {(e.tx_hash, e.log_index, e.address) for e in reb}
            for e in extra:
                key = (e.tx_hash, e.log_index, e.address)
                if key not in seen:
                    reb.append(e)
                    seen.add(key)
        snap = build_mm_pair_snapshot(
            pair_id=pair_id,
            labels=labels,
            inventory_events=inv,
            rebalance_events=reb,
            generated_ts_ms=now_ms(),
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
