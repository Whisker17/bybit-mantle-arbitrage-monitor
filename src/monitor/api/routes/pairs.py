"""GET /api/pairs and pair detail / trades."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from monitor.api.serialize import to_json_dict, to_jsonable
from monitor.api.state import AppState, app_state_from_request
from monitor.metrics.pnl_snapshot import (
    PnlPairSnapshot,
    build_pnl_pair_snapshot,
    overview_pnl_summary,
)
from monitor.quotes import now_ms
from monitor.storage import JournalReader
from monitor.symbols.models import Pair
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
        now_ms=now_ms(),
        stale_ms=state.api.collector_stale_ms,
    )
    if cache is not None:
        cache.put(pair.id, snap)
    return snap


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
    """Overview table model (one row per configured pair) + PnL v2 optimal summary."""
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
        rows_out: list[dict[str, Any]] = []
        for row in body["rows"]:
            pair = _pair_or_404(state, row["pair_id"])
            snap = _pnl_snapshot_for_pair(state, pair=pair, reader=reader)
            enriched = dict(row)
            enriched["pnl_v2"] = overview_pnl_summary(snap).to_dict()
            rows_out.append(enriched)
        body["rows"] = rows_out
        return body


@router.get("/api/pairs/{pair_id}")
def get_pair(pair_id: str, request: Request) -> dict[str, Any]:
    """Detail model for one pair + full PnL v2 bucket tables (both directions)."""
    state = app_state_from_request(request)
    model, pnl = _detail_model(state, pair_id)
    body = to_json_dict(model)
    body["pnl_v2"] = pnl.to_dict()
    # Keep overview row in sync with the same snapshot (summary view).
    if "overview" in body and isinstance(body["overview"], dict):
        body["overview"] = dict(body["overview"])
        body["overview"]["pnl_v2"] = overview_pnl_summary(pnl).to_dict()
    return body


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
