"""FastAPI application factory for the read-only panel API (WHI-757 / WHI-774)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from monitor.api.config import ApiConfig, load_api_config
from monitor.api.pnl_cache import PnlSnapshotCache
from monitor.api.routes import health as health_routes
from monitor.api.routes import markets as markets_routes
from monitor.api.routes import pairs as pairs_routes
from monitor.api.state import AppState, InventoryEventsCache, MarketRuntime
from monitor.markets import (
    DEFAULT_MARKET_ID,
    list_market_ids,
    load_market_context,
    normalize_market_id,
)
from monitor.storage import JournalReader
from monitor.tui.config import TuiConfig, load_tui_config, validate_tui_against_metrics


def _inventory_pair_count(inventory: dict[str, Any]) -> int:
    pairs = inventory.get("pairs")
    if isinstance(pairs, list):
        return len(pairs)
    return 0


def _build_market_runtime(
    *,
    market_id: str,
    api: ApiConfig,
    tui: TuiConfig,
    sqlite_override: Path | None,
) -> MarketRuntime:
    """Assemble one market's configs + optional journal reader."""
    mid = normalize_market_id(market_id)
    ctx = load_market_context(mid, sqlite_path=sqlite_override, load_collector=True)
    # Metrics/tui cross-check once per market (costs differ; sizes shared).
    validate_tui_against_metrics(tui, ctx.metrics)
    db_path = ctx.sqlite_path
    reader: JournalReader | None
    if db_path.is_file():
        reader = JournalReader(db_path)
    else:
        reader = None
    pair_count = (
        len(ctx.pairs.pairs)
        if ctx.pairs is not None
        else _inventory_pair_count(ctx.market_file.inventory)
    )
    return MarketRuntime(
        market_id=mid,
        display_name=ctx.display_name,
        has_rfq=ctx.dex.has_rfq,
        cex_venue=ctx.cex.venue,
        dex_venue=ctx.dex.venue,
        pairs=ctx.pairs,
        pair_count=pair_count,
        metrics=ctx.metrics,
        attribution=ctx.attribution,
        tui=tui,
        db_path=db_path,
        reader=reader,
        pnl_cache=PnlSnapshotCache(ttl_s=api.pnl_cache_ttl_s),
        inventory_cache=InventoryEventsCache(ttl_s=api.mm_inventory_cache_ttl_s),
    )


def build_app_state(
    *,
    api_config_path: Path | None = None,
    api: ApiConfig | None = None,
    market_id: str | None = None,
    market_ids: list[str] | None = None,
) -> AppState:
    """Load configs and open per-market journal readers when DB files exist.

    Loads **all** known markets (``config/markets/*.yaml``) so the Web market
    switcher can read both journals. ``market_id`` / ``MONITOR_MARKET`` only
    selects the default market for legacy unscoped routes (``/api/pairs``).
    """
    cfg = api if api is not None else load_api_config(api_config_path)
    # MONITOR_MARKET is process bootstrap for uvicorn --reload workers only
    # (factory apps cannot take kwargs across reload). Not a general config knob.
    default_mid = normalize_market_id(
        market_id
        or os.environ.get("MONITOR_MARKET")
        or cfg.market
        or DEFAULT_MARKET_ID
    )
    ids = market_ids if market_ids is not None else list_market_ids()
    if not ids:
        ids = [default_mid]
    # Always include the default market even if markets dir is incomplete.
    if default_mid not in ids:
        ids = sorted({*ids, default_mid})

    tui = load_tui_config()
    runtimes: dict[str, MarketRuntime] = {}
    for mid in ids:
        # When the market matches api.yaml, honour api.sqlite_path (ops override).
        sqlite_override: Path | None = None
        if mid == normalize_market_id(cfg.market):
            sqlite_override = cfg.resolved_sqlite_path()
        runtimes[mid] = _build_market_runtime(
            market_id=mid,
            api=cfg,
            tui=tui,
            sqlite_override=sqlite_override,
        )

    return AppState(
        api=cfg,
        tui=tui,
        markets=runtimes,
        default_market_id=default_mid,
    )


def create_app(
    *,
    api_config_path: Path | None = None,
    api: ApiConfig | None = None,
    state: AppState | None = None,
    market_id: str | None = None,
) -> FastAPI:
    """Build the ASGI app. Prefer ``python -m monitor.api`` for production."""

    # Resolve API config once so CORS and lifespan share the same object.
    # Prefer explicit state.api / api over a second disk load.
    if state is not None:
        resolved_api = state.api
        app_state: AppState | None = state
    else:
        resolved_api = api if api is not None else load_api_config(api_config_path)
        app_state = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal app_state
        if app_state is None:
            app_state = build_app_state(
                api_config_path=api_config_path,
                api=resolved_api,
                market_id=market_id,
            )
        app.state.app_state = app_state
        try:
            yield
        finally:
            if app_state is not None:
                app_state.close()

    application = FastAPI(
        title="xStocks arbitrage monitor API",
        description=(
            "Read-only JSON over per-market collector SQLite journals. "
            "Reuses monitor.tui builders + M3/M4 metrics (WHI-757 / WHI-774). "
            "Clients should poll GET /api/{market}/pairs and GET /api/{market}/health "
            "every ~2s (see config/api.yaml poll_interval_s). "
            "Legacy unscoped /api/pairs and /api/health map to the default market."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS empty on VPS (nginx same-origin). Local dogfood can list origins.
    if resolved_api.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved_api.cors_origins),
            allow_methods=["GET"],
            allow_headers=["*"],
        )

    application.include_router(markets_routes.router)
    application.include_router(health_routes.router)
    application.include_router(pairs_routes.router)

    @application.get("/")
    def root() -> dict[str, Any]:
        """Tiny discovery index (OpenAPI is the real contract)."""
        return {
            "service": "monitor.api",
            "docs": "/docs",
            "openapi": "/openapi.json",
            "markets": "/api/markets",
            "health": "/api/health",
            "pairs": "/api/pairs",
            "market_health": "/api/{market}/health",
            "market_pairs": "/api/{market}/pairs",
        }

    return application
