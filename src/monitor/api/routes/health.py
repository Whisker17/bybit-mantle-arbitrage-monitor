"""GET /api/health."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from monitor.api.health import HealthStatus, build_health
from monitor.api.serialize import to_json_dict
from monitor.api.state import app_state_from_request

router = APIRouter(tags=["health"])


@router.get("/api/health")
def get_health(request: Request) -> dict[str, Any]:
    """Collector alive flag, latest block, recent gaps, poll interval hint."""
    state = app_state_from_request(request)
    reader = state.ensure_reader()
    if reader is None:
        return to_json_dict(
            HealthStatus.unavailable(
                db_path=str(state.db_path),
                poll_interval_s=state.api.poll_interval_s,
                error=f"collector journal not found: {state.db_path}",
            )
        )
    with state.lock:
        status = build_health(
            reader,
            stale_ms=state.api.collector_stale_ms,
            gap_window_ms=state.api.recent_gap_window_ms,
            poll_interval_s=state.api.poll_interval_s,
        )
    return to_json_dict(status)
