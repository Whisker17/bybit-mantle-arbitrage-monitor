"""GET /api/pairs and pair detail / trades."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from monitor.api.serialize import to_json_dict, to_jsonable
from monitor.api.state import AppState
from monitor.symbols.models import Pair
from monitor.tui.builder import build_overview, build_pair_detail

router = APIRouter(tags=["pairs"])


def _state(request: Request) -> AppState:
    return request.app.state.app_state  # type: ignore[no-any-return]


def _require_reader(state: AppState) -> None:
    if state.reader is None:
        raise HTTPException(
            status_code=503,
            detail=f"collector journal not found: {state.db_path}",
        )


def _pair_or_404(state: AppState, pair_id: str) -> Pair:
    try:
        return state.pairs.pair_by_id(pair_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown pair_id: {pair_id}") from exc


@router.get("/api/pairs")
def list_pairs(request: Request) -> dict[str, Any]:
    """Overview table model (one row per configured pair)."""
    state = _state(request)
    _require_reader(state)
    assert state.reader is not None
    model = build_overview(
        pairs=state.pairs,
        reader=state.reader,
        metrics=state.metrics,
        tui=state.tui,
        edge_state=state.edge_state,
    )
    return to_json_dict(model)


@router.get("/api/pairs/{pair_id}")
def get_pair(pair_id: str, request: Request) -> dict[str, Any]:
    """Detail model for one pair (overview + edges + trades + attribution)."""
    state = _state(request)
    _require_reader(state)
    assert state.reader is not None
    pair = _pair_or_404(state, pair_id)
    model = build_pair_detail(
        pair=pair,
        reader=state.reader,
        metrics=state.metrics,
        attribution_cfg=state.attribution,
        tui=state.tui,
        edge_state=state.edge_state,
    )
    return to_json_dict(model)


@router.get("/api/pairs/{pair_id}/trades")
def get_pair_trades(pair_id: str, request: Request) -> dict[str, Any]:
    """Trade stream for one pair (subset of the detail model)."""
    state = _state(request)
    _require_reader(state)
    assert state.reader is not None
    pair = _pair_or_404(state, pair_id)
    model = build_pair_detail(
        pair=pair,
        reader=state.reader,
        metrics=state.metrics,
        attribution_cfg=state.attribution,
        tui=state.tui,
        edge_state=state.edge_state,
    )
    return {
        "pair_id": model.pair_id,
        "generated_ts_ms": model.generated_ts_ms,
        "trades": to_jsonable(model.trades),
    }
