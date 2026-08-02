"""Shared route helpers for multi-market API paths (WHI-774)."""

from __future__ import annotations

from fastapi import HTTPException

from monitor.api.state import AppState, MarketRuntime
from monitor.markets.ids import normalize_market_id


def runtime_or_404(state: AppState, market: str | None) -> MarketRuntime:
    """Resolve a market runtime or 404 unknown market id."""
    mid = normalize_market_id(market) if market else state.default_market_id
    try:
        return state.market(mid)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown market: {mid}") from exc
