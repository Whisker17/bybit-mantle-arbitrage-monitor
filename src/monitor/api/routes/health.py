"""GET /api/health."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from monitor.api.health import build_health, missing_db_health
from monitor.api.serialize import to_json_dict
from monitor.api.state import AppState

router = APIRouter(tags=["health"])


def _state(request: Request) -> AppState:
    return request.app.state.app_state  # type: ignore[no-any-return]


@router.get("/api/health")
def get_health(request: Request) -> dict[str, Any]:
    """Collector alive flag, latest block, recent gaps, poll interval hint."""
    state = _state(request)
    if state.reader is None:
        return to_json_dict(
            missing_db_health(
                db_path=str(state.db_path),
                poll_interval_s=state.api.poll_interval_s,
                error=f"collector journal not found: {state.db_path}",
            )
        )
    status = build_health(
        state.reader,
        stale_ms=state.api.collector_stale_ms,
        gap_window_ms=state.api.recent_gap_window_ms,
        poll_interval_s=state.api.poll_interval_s,
    )
    return to_json_dict(status)
