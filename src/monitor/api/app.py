"""FastAPI application factory for the read-only panel API (WHI-757)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from monitor.api.config import ApiConfig, load_api_config
from monitor.api.pnl_cache import PnlSnapshotCache
from monitor.api.routes import health as health_routes
from monitor.api.routes import pairs as pairs_routes
from monitor.api.state import AppState, InventoryEventsCache
from monitor.attribution.config import load_attribution_config
from monitor.metrics.config import load_metrics_config
from monitor.storage import JournalReader
from monitor.symbols import load_pairs_config
from monitor.tui.config import load_tui_config, validate_tui_against_metrics


def build_app_state(
    *,
    api_config_path: Path | None = None,
    api: ApiConfig | None = None,
) -> AppState:
    """Load configs and open the journal reader when the DB file exists."""
    cfg = api if api is not None else load_api_config(api_config_path)
    pairs = load_pairs_config()
    metrics = load_metrics_config()
    attribution = load_attribution_config()
    tui = load_tui_config()
    # Builder reference size / history windows come from tui.yaml (single source).
    validate_tui_against_metrics(tui, metrics)
    db_path = cfg.resolved_sqlite_path()
    reader: JournalReader | None
    if db_path.is_file():
        reader = JournalReader(db_path)
    else:
        reader = None
    return AppState(
        api=cfg,
        pairs=pairs,
        metrics=metrics,
        attribution=attribution,
        tui=tui,
        db_path=db_path,
        reader=reader,
        pnl_cache=PnlSnapshotCache(ttl_s=cfg.pnl_cache_ttl_s),
        inventory_cache=InventoryEventsCache(ttl_s=cfg.mm_inventory_cache_ttl_s),
    )


def create_app(
    *,
    api_config_path: Path | None = None,
    api: ApiConfig | None = None,
    state: AppState | None = None,
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
            app_state = build_app_state(api_config_path=api_config_path, api=resolved_api)
        app.state.app_state = app_state
        try:
            yield
        finally:
            if app_state is not None:
                app_state.close()

    application = FastAPI(
        title="xStocks arbitrage monitor API",
        description=(
            "Read-only JSON over the collector SQLite journal. "
            "Reuses monitor.tui builders + M3/M4 metrics (WHI-757). "
            "Clients should poll GET /api/pairs and GET /api/health "
            "every ~2s (see config/api.yaml poll_interval_s)."
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

    application.include_router(health_routes.router)
    application.include_router(pairs_routes.router)

    @application.get("/")
    def root() -> dict[str, Any]:
        """Tiny discovery index (OpenAPI is the real contract)."""
        return {
            "service": "monitor.api",
            "docs": "/docs",
            "openapi": "/openapi.json",
            "health": "/api/health",
            "pairs": "/api/pairs",
        }

    return application
