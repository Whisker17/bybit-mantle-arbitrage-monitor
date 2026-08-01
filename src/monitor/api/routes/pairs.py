"""GET /api/pairs and pair detail / trades."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from monitor.api.serialize import to_json_dict, to_jsonable
from monitor.api.state import AppState, app_state_from_request
from monitor.storage import JournalReader
from monitor.symbols.models import Pair
from monitor.tui.builder import build_overview, build_pair_detail
from monitor.tui.model import PairDetailModel

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


def _detail_model(state: AppState, pair_id: str) -> PairDetailModel:
    """Build detail under the process lock (shared by detail + trades routes)."""
    reader = _require_reader(state)
    pair = _pair_or_404(state, pair_id)
    with state.lock:
        return build_pair_detail(
            pair=pair,
            reader=reader,
            metrics=state.metrics,
            attribution_cfg=state.attribution,
            tui=state.tui,
            edge_state=state.edge_state,
        )


@router.get("/api/pairs")
def list_pairs(request: Request) -> dict[str, Any]:
    """Overview table model (one row per configured pair)."""
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
    return to_json_dict(model)


@router.get("/api/pairs/{pair_id}")
def get_pair(pair_id: str, request: Request) -> dict[str, Any]:
    """Detail model for one pair (overview + edges + trades + attribution)."""
    state = app_state_from_request(request)
    return to_json_dict(_detail_model(state, pair_id))


@router.get("/api/pairs/{pair_id}/trades")
def get_pair_trades(pair_id: str, request: Request) -> dict[str, Any]:
    """Trade stream for one pair (subset of the detail model).

    Skeleton reuses ``build_pair_detail`` so trade labels stay identical to the
    detail page; a cheaper path can land when poll load requires it.
    """
    state = app_state_from_request(request)
    model = _detail_model(state, pair_id)
    return {
        "pair_id": model.pair_id,
        "generated_ts_ms": model.generated_ts_ms,
        "trades": to_jsonable(model.trades),
    }
