"""GET /api/health and GET /api/{market}/health (WHI-774)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from monitor.api.health import HealthStatus, build_health
from monitor.api.serialize import to_json_dict
from monitor.api.state import AppState, MarketRuntime, app_state_from_request
from monitor.markets.ids import normalize_market_id

router = APIRouter(tags=["health"])


def _runtime_or_404(state: AppState, market: str | None) -> MarketRuntime:
    mid = normalize_market_id(market) if market else state.default_market_id
    try:
        return state.market(mid)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown market: {mid}") from exc


def _health_body(state: AppState, runtime: MarketRuntime) -> dict[str, Any]:
    reader = runtime.ensure_reader()
    if reader is None:
        body = to_json_dict(
            HealthStatus.unavailable(
                db_path=str(runtime.db_path),
                poll_interval_s=state.api.poll_interval_s,
                error=f"collector journal not found: {runtime.db_path}",
            )
        )
    else:
        with runtime.lock:
            status = build_health(
                reader,
                stale_ms=state.api.collector_stale_ms,
                gap_window_ms=state.api.recent_gap_window_ms,
                poll_interval_s=state.api.poll_interval_s,
            )
        body = to_json_dict(status)
    body["market_id"] = runtime.market_id
    body["display_name"] = runtime.display_name
    body["has_rfq"] = runtime.has_rfq
    body["data_status"] = runtime.data_status()
    return body


@router.get("/api/health")
def get_health(request: Request) -> dict[str, Any]:
    """Legacy unscoped health → default market (bookmark / old client compatible)."""
    state = app_state_from_request(request)
    runtime = _runtime_or_404(state, None)
    return _health_body(state, runtime)


@router.get("/api/{market}/health")
def get_market_health(market: str, request: Request) -> dict[str, Any]:
    """Per-market collector alive flag, latest block, recent gaps."""
    state = app_state_from_request(request)
    runtime = _runtime_or_404(state, market)
    return _health_body(state, runtime)
