"""GET /api/markets — multi-market list + per-market health summary (WHI-774)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from monitor.api.state import MarketRuntime, app_state_from_request

router = APIRouter(tags=["markets"])


def market_summary(
    runtime: MarketRuntime,
    *,
    stale_ms: int,
    gap_window_ms: int,
    poll_interval_s: float,
    rfq_error_window_ms: int,
) -> dict[str, Any]:
    """One market card for the switcher / discovery endpoint."""
    from monitor.api.routes.health import health_dict_for_runtime

    health = health_dict_for_runtime(
        runtime,
        stale_ms=stale_ms,
        gap_window_ms=gap_window_ms,
        poll_interval_s=poll_interval_s,
        rfq_error_window_ms=rfq_error_window_ms,
    )
    return {
        "id": runtime.market_id,
        "display_name": runtime.display_name,
        "has_rfq": runtime.has_rfq,
        "cex_venue": runtime.cex_venue,
        "dex_venue": runtime.dex_venue,
        "pair_count": runtime.pair_count,
        "data_status": runtime.data_status(),
        "db_path": str(runtime.db_path),
        "health": health,
    }


@router.get("/api/markets")
def list_markets(request: Request) -> dict[str, Any]:
    """Market list + health summary for the Web market switcher."""
    state = app_state_from_request(request)
    markets = [
        market_summary(
            runtime,
            stale_ms=state.api.collector_stale_ms,
            gap_window_ms=state.api.recent_gap_window_ms,
            poll_interval_s=state.api.poll_interval_s,
            rfq_error_window_ms=state.api.rfq_error_window_ms,
        )
        for runtime in sorted(state.markets.values(), key=lambda r: r.market_id)
    ]
    return {
        "default_market_id": state.default_market_id,
        "poll_interval_s": state.api.poll_interval_s,
        "markets": markets,
    }
