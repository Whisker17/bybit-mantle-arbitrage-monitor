"""GET /api/health and GET /api/{market}/health (WHI-774)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from monitor.api.health import HealthStatus, build_health
from monitor.api.routes.common import runtime_or_404
from monitor.api.serialize import to_json_dict
from monitor.api.state import AppState, MarketRuntime, app_state_from_request

router = APIRouter(tags=["health"])


def health_dict_for_runtime(
    runtime: MarketRuntime,
    *,
    stale_ms: int,
    gap_window_ms: int,
    poll_interval_s: float,
) -> dict[str, Any]:
    """Shared health snapshot used by /health and /api/markets summaries."""
    reader = runtime.ensure_reader()
    if reader is None:
        return to_json_dict(
            HealthStatus.unavailable(
                db_path=str(runtime.db_path),
                poll_interval_s=poll_interval_s,
                error=f"collector journal not found: {runtime.db_path}",
            )
        )
    with runtime.lock:
        status = build_health(
            reader,
            stale_ms=stale_ms,
            gap_window_ms=gap_window_ms,
            poll_interval_s=poll_interval_s,
        )
    return to_json_dict(status)


def _health_body(state: AppState, runtime: MarketRuntime) -> dict[str, Any]:
    body = health_dict_for_runtime(
        runtime,
        stale_ms=state.api.collector_stale_ms,
        gap_window_ms=state.api.recent_gap_window_ms,
        poll_interval_s=state.api.poll_interval_s,
    )
    body["market_id"] = runtime.market_id
    body["display_name"] = runtime.display_name
    body["has_rfq"] = runtime.has_rfq
    body["data_status"] = runtime.data_status()
    return body


@router.get("/api/health")
def get_health(request: Request) -> dict[str, Any]:
    """Legacy unscoped health → default market (bookmark / old client compatible)."""
    state = app_state_from_request(request)
    runtime = runtime_or_404(state, None)
    return _health_body(state, runtime)


@router.get("/api/{market}/health")
def get_market_health(market: str, request: Request) -> dict[str, Any]:
    """Per-market collector alive flag, latest block, recent gaps."""
    state = app_state_from_request(request)
    runtime = runtime_or_404(state, market)
    return _health_body(state, runtime)
