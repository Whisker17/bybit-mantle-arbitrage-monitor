"""GET /api/pairs and market-scoped pair detail / trades / mm (WHI-769 / WHI-774)."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from monitor.api.routes.common import runtime_or_404
from monitor.api.serialize import to_json_dict, to_jsonable
from monitor.api.state import AppState, MarketRuntime, app_state_from_request
from monitor.attribution.address_labels import inventory_events_from_ticks
from monitor.attribution.mm_draft import InventoryEvent
from monitor.attribution.mm_panel import (
    MM_LABEL,
    MmActiveStatus,
    build_address_panel_rows,
    build_mm_pair_snapshot,
    labels_by_address,
    mm_active_status,
    pair_active_addresses,
)
from monitor.metrics.amm_pool import amm_pool_from_pair_tick
from monitor.metrics.drift import (
    drift_wire_for_pair,
    net_bps_from_pnl_tables,
)
from monitor.metrics.pnl_snapshot import (
    PnlOptimalSummary,
    PnlPairSnapshot,
    build_pnl_pair_snapshot,
    overview_pnl_summary,
)
from monitor.metrics.withdrawal import withdrawal_params_from_pair
from monitor.quotes import now_ms
from monitor.storage import JournalReader
from monitor.storage.reader import AddressLabelRow
from monitor.symbols.bstocks_models import BStocksPair, BStocksPairsConfig
from monitor.symbols.models import Pair, PairsConfig
from monitor.symbols.token_map import quote_is_token0_by_pair
from monitor.tui.builder import build_overview, build_pair_detail
from monitor.tui.model import PairDetailModel

router = APIRouter(tags=["pairs"])

_ACCUMULATING_MSG = "Market data accumulating"
InventoryConfig = PairsConfig | BStocksPairsConfig
InventoryPair = Pair | BStocksPair


def _require_reader(runtime: MarketRuntime) -> JournalReader:
    reader = runtime.ensure_reader()
    if reader is None:
        raise HTTPException(
            status_code=503,
            detail=f"collector journal not found: {runtime.db_path}",
        )
    return reader


def _require_inventory(runtime: MarketRuntime) -> InventoryConfig:
    """Return wired inventory (Fluxion or bStocks) or 503 when not builder-ready."""
    inv = runtime.inventory_pairs()
    if inv is None:
        raise HTTPException(
            status_code=503,
            detail=(
                f"market {runtime.market_id!r} inventory is not yet wired for "
                f"pair builders ({_ACCUMULATING_MSG})"
            ),
        )
    return inv


def _require_pairs(runtime: MarketRuntime) -> PairsConfig:
    """Fluxion-only inventory (legacy call sites that need wrapper/RFQ maps)."""
    pairs = runtime.pairs
    if pairs is None:
        raise HTTPException(
            status_code=503,
            detail=(
                f"market {runtime.market_id!r} requires Fluxion PairsConfig "
                f"for this endpoint ({_ACCUMULATING_MSG})"
            ),
        )
    return pairs


def _pair_or_404(runtime: MarketRuntime, pair_id: str) -> InventoryPair:
    inv = _require_inventory(runtime)
    try:
        return inv.pair_by_id(pair_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown pair_id: {pair_id}") from exc


def _native_decimals(pair: InventoryPair) -> int:
    if isinstance(pair, BStocksPair):
        return pair.pancake.native_decimals
    return pair.fluxion.native_decimals


def _quote_is_token0_map(inv: InventoryConfig) -> dict[str, bool]:
    """USDC/USDT is token0 when its address sorts before the base token."""
    if isinstance(inv, PairsConfig):
        return quote_is_token0_by_pair(inv)
    out: dict[str, bool] = {}
    for p in inv.pairs:
        q = p.pancake.quote_token_address.lower()
        base = p.pancake.native_token.lower()
        out[p.id] = q < base
    return out


def _market_fields(runtime: MarketRuntime) -> dict[str, Any]:
    return {
        "market_id": runtime.market_id,
        "display_name": runtime.display_name,
        "has_rfq": runtime.has_rfq,
        "data_status": runtime.data_status(),
    }


def _enrich_overview_row(
    row: dict[str, Any],
    *,
    summary: PnlOptimalSummary,
    pair: InventoryPair | None,
    k: Decimal,
    tables: Mapping[Any, Any] | None = None,
    drift: dict[str, Any] | None = None,
    session_fallback: str | None = None,
) -> dict[str, Any]:
    """PnL flat keys + Net@Q* + sequential drift bar (WHI-966 + WHI-962).

    Single seam for list + detail overview enrichment so a new wire field
    cannot land on one path and miss the other (see
    ``PnlOptimalSummary.overview_enrichment_wire``). Pass ``drift`` when the
    annotation was already built for ``pnl_v2.drift`` (avoid double compute).

    ``session_fallback`` is overview ``session_now`` when the row has no
    per-pair session (no ticks yet) so inventory σ still selects open/closed.
    """
    enriched = dict(row)
    enriched.update(summary.overview_enrichment_wire())
    if drift is not None:
        enriched.update(drift)
    else:
        inv_pair = pair if isinstance(pair, (Pair, BStocksPair)) else None
        nets = None
        if tables is not None:
            nets = net_bps_from_pnl_tables(tables)
        sess = row.get("session") or session_fallback
        enriched.update(
            drift_wire_for_pair(
                pair=inv_pair,
                session=sess,
                k=k,
                optimal_direction=summary.direction,
                optimal_net_bps=summary.optimal_net_pnl_bps,
                net_bps_by_direction=nets,
            )
        )
    return enriched


def _empty_overview(state: AppState, runtime: MarketRuntime, *, error: str) -> dict[str, Any]:
    """Explicit accumulating overview (no dashed placeholder)."""
    from datetime import UTC, datetime

    from monitor.metrics.session import SessionKind, session_kind

    ts = now_ms()
    try:
        sess = session_kind(
            datetime.fromtimestamp(ts / 1000, tz=UTC),
            config=runtime.metrics,
        ).value
    except ValueError:
        # Holiday table year not covered yet — treat as closed, not "unknown crash".
        sess = SessionKind.CLOSED.value
    tui = runtime.tui
    return {
        "generated_ts_ms": ts,
        "session_now": sess,
        "sort_key": tui.default_sort,
        "sort_desc": tui.default_sort_desc,
        "reference_size_usd": format(tui.reference_size_usd, "f"),
        "rows": [],
        "db_path": str(runtime.db_path),
        "error": error,
        **_market_fields(runtime),
    }


def _pnl_snapshot_for_pair(
    runtime: MarketRuntime,
    state: AppState,
    *,
    pair: InventoryPair,
    reader: JournalReader,
) -> PnlPairSnapshot:
    """Load ticks + build PnL snapshot, with optional TTL cache (caller holds lock)."""
    cache = runtime.pnl_cache
    if cache is not None:
        hit = cache.get(pair.id)
        if hit is not None:
            return hit

    bybit = reader.latest_bybit_book(pair.id)
    amm_tick = reader.latest_pool_state(pair.id)
    depth = reader.latest_bybit_depth(pair.id)
    rfq_buy, rfq_sell = reader.latest_rfq_sides(pair.id)
    amm = (
        amm_pool_from_pair_tick(
            pair, amm_tick, quote_decimals=runtime.quote_decimals
        )
        if amm_tick is not None
        else None
    )
    wd = withdrawal_params_from_pair(pair)
    snap = build_pnl_pair_snapshot(
        pair_id=pair.id,
        bybit=bybit,
        amm=amm,
        amm_tick=amm_tick,
        config=runtime.metrics,
        depth=depth,
        rfq_buy=rfq_buy,
        rfq_sell=rfq_sell,
        native_decimals=_native_decimals(pair),
        # Same config switch as attribution (market dex.has_rfq via assembly).
        rfq_enabled=runtime.attribution.has_rfq,
        now_ms=now_ms(),
        # Per-market override (config/markets/*.yaml) else api.yaml default.
        quote_max_age_ms=(
            runtime.quote_max_age_ms
            if runtime.quote_max_age_ms is not None
            else state.api.quote_max_age_ms
        ),
        asset_withdrawal_fee_tokens=wd.asset_fee_tokens,
        price_multiplier=wd.price_multiplier,
    )
    if cache is not None:
        cache.put(pair.id, snap)
    return snap


def _inventory_events(
    runtime: MarketRuntime, reader: JournalReader
) -> list[InventoryEvent]:
    """Ledger events for MM activity / inventory curves (caller holds lock)."""
    cache = runtime.inventory_cache
    if cache is not None:
        hit = cache.get()
        if hit is not None:
            return hit
    inv = _require_inventory(runtime)
    q0 = _quote_is_token0_map(inv)
    events = inventory_events_from_ticks(
        swaps=reader.recent_swaps(),
        rfq_fills=reader.recent_rfq_fills(),
        transfers=reader.recent_erc20_transfers(),
        quote_is_token0_by_pair=q0,
    )
    if cache is not None:
        cache.put(events)
    return events


def _mm_active_for(
    state: AppState,
    *,
    pair_id: str,
    label_count: int,
    mm_labels: list[AddressLabelRow],
    inv_events: list[InventoryEvent],
    now: int,
) -> MmActiveStatus:
    """Shared overview badge path for list + detail (caller holds lock)."""
    return mm_active_status(
        label_count=label_count,
        mm_labels=mm_labels,
        inventory_events=inv_events,
        pair_id=pair_id,
        now_ms=now,
        window_ms=state.api.mm_active_window_ms,
    )


def _detail_model(
    state: AppState, runtime: MarketRuntime, pair_id: str
) -> tuple[PairDetailModel, PnlPairSnapshot]:
    """Build detail + PnL under the process lock (shared by detail + trades)."""
    # Pairs check first so accumulating markets report "not wired" not "no journal".
    pair = _pair_or_404(runtime, pair_id)
    reader = _require_reader(runtime)
    inv = _require_inventory(runtime)
    threshold = Decimal(str(inv.low_liquidity_threshold_usd))
    with runtime.lock:
        model = build_pair_detail(
            pair=pair,
            reader=reader,
            metrics=runtime.metrics,
            attribution_cfg=runtime.attribution,
            tui=runtime.tui,
            edge_state=runtime.edge_state,
            low_liquidity_threshold_usd=threshold,
        )
        pnl = _pnl_snapshot_for_pair(runtime, state, pair=pair, reader=reader)
        return model, pnl


def _list_pairs_body(state: AppState, runtime: MarketRuntime) -> dict[str, Any]:
    """Overview table body for one market (shared by legacy + scoped routes).

    Builder-ready markets (Fluxion ``pairs`` or bStocks inventory) with a
    missing journal keep the 503 contract. Markets with no inventory shape at
    all return a 200 accumulating empty state.
    """
    inv = runtime.inventory_pairs()
    if inv is None:
        return _empty_overview(state, runtime, error=_ACCUMULATING_MSG)

    reader = _require_reader(runtime)

    with runtime.lock:
        model = build_overview(
            pairs=inv,
            reader=reader,
            metrics=runtime.metrics,
            tui=runtime.tui,
            edge_state=runtime.edge_state,
        )
        body = to_json_dict(model)
        body.update(_market_fields(runtime))
        label_count = reader.address_label_count()
        mm_labels = (
            reader.address_labels(label=MM_LABEL) if label_count > 0 else []
        )
        inv_events = (
            _inventory_events(runtime, reader)
            if label_count > 0 and mm_labels
            else []
        )
        ts = now_ms()
        k = runtime.metrics.pnl_v2.drift_premium_k
        session_now = body.get("session_now")
        if isinstance(session_now, str):
            session_fallback: str | None = session_now
        else:
            session_fallback = None
        rows_out: list[dict[str, Any]] = []
        for row in body["rows"]:
            pair_id = row["pair_id"]
            try:
                pair = inv.pair_by_id(pair_id)
            except KeyError:
                # Builder rows should always be configured pairs; never 404 the list.
                empty = PnlOptimalSummary(status="no_pool", has_depth=False)
                enriched = _enrich_overview_row(
                    row,
                    summary=empty,
                    pair=None,
                    k=k,
                    session_fallback=session_fallback,
                )
                enriched["mm_active"] = "unknown"
                rows_out.append(enriched)
                continue
            snap = _pnl_snapshot_for_pair(runtime, state, pair=pair, reader=reader)
            summary = overview_pnl_summary(snap)
            # WHI-824/966 + WHI-962: flat sort + Net@Q* + drift in one seam.
            enriched = _enrich_overview_row(
                row,
                summary=summary,
                pair=pair,
                k=k,
                tables=snap.tables,
                session_fallback=session_fallback,
            )
            enriched["mm_active"] = _mm_active_for(
                state,
                pair_id=pair_id,
                label_count=label_count,
                mm_labels=mm_labels,
                inv_events=inv_events,
                now=ts,
            )
            rows_out.append(enriched)
        body["rows"] = rows_out
        return body


def _get_pair_body(
    state: AppState, runtime: MarketRuntime, pair_id: str
) -> dict[str, Any]:
    model, pnl = _detail_model(state, runtime, pair_id)
    reader = _require_reader(runtime)
    pair = _pair_or_404(runtime, pair_id)
    body = to_json_dict(model)
    body["pnl_v2"] = pnl.to_dict()
    body.update(_market_fields(runtime))

    k = runtime.metrics.pnl_v2.drift_premium_k
    ov_summary = overview_pnl_summary(pnl)
    session = None
    if "overview" in body and isinstance(body["overview"], dict):
        session = body["overview"].get("session")
    if not session:
        session = body.get("session_now")
    inv_pair = pair if isinstance(pair, (Pair, BStocksPair)) else None
    drift = drift_wire_for_pair(
        pair=inv_pair,
        session=session if isinstance(session, str) else None,
        k=k,
        optimal_direction=ov_summary.direction,
        optimal_net_bps=ov_summary.optimal_net_pnl_bps,
        net_bps_by_direction=net_bps_from_pnl_tables(pnl.tables),
    )
    # Nested under pnl_v2 for the detail requirement breakdown; reuse the
    # same dict on overview enrichment (no double annotate_drift).
    body["pnl_v2"] = {**body["pnl_v2"], "drift": drift}

    with runtime.lock:
        label_count = reader.address_label_count()
        all_labels = reader.address_labels() if label_count > 0 else []
        mm_labels = [r for r in all_labels if r.label == MM_LABEL]
        inv = (
            _inventory_events(runtime, reader)
            if label_count > 0 and mm_labels
            else []
        )
        if "overview" in body and isinstance(body["overview"], dict):
            # WHI-824/966 + WHI-962: same enrichment seam as list path.
            body["overview"] = _enrich_overview_row(
                body["overview"],
                summary=ov_summary,
                pair=inv_pair,
                k=k,
                drift=drift,
            )
            body["overview"]["mm_active"] = _mm_active_for(
                state,
                pair_id=pair_id,
                label_count=label_count,
                mm_labels=mm_labels,
                inv_events=inv,
                now=now_ms(),
            )
        labels = labels_by_address(all_labels)
        active = pair_active_addresses(inv, pair_id) if inv else set()
        # Also mark addresses that appear as AMM top takers so they stay in panel.
        if model.attribution is not None:
            for t in model.attribution.top_takers:
                active.add(t.address.lower())
        panel_rows = build_address_panel_rows(
            attribution=model.attribution,
            labels=labels,
            top_n=runtime.attribution.top_takers_n,
            pair_active=active if label_count > 0 else None,
        )
    body["address_panel"] = [r.to_dict() for r in panel_rows]
    return body


def _get_pair_mm_body(
    state: AppState, runtime: MarketRuntime, pair_id: str
) -> dict[str, Any]:
    _pair_or_404(runtime, pair_id)
    reader = _require_reader(runtime)
    with runtime.lock:
        labels = reader.address_labels()
        inv = _inventory_events(runtime, reader)
        reb = reader.rebalance_events(
            pair_id=pair_id, limit=state.api.mm_rebalance_limit
        )
        snap = build_mm_pair_snapshot(
            pair_id=pair_id,
            labels=labels,
            inventory_events=inv,
            rebalance_events=reb,
            generated_ts_ms=now_ms(),
            max_series_points=state.api.mm_series_max_points,
            max_rebalance=state.api.mm_rebalance_limit,
        )
        body = snap.to_dict()
        body.update(_market_fields(runtime))
        return body


def _get_pair_trades_body(
    state: AppState, runtime: MarketRuntime, pair_id: str
) -> dict[str, Any]:
    model, _pnl = _detail_model(state, runtime, pair_id)
    return {
        "pair_id": model.pair_id,
        "generated_ts_ms": model.generated_ts_ms,
        "trades": to_jsonable(model.trades),
        **_market_fields(runtime),
    }


# --- Legacy unscoped routes (default market) ---------------------------------


@router.get("/api/pairs")
def list_pairs(request: Request) -> dict[str, Any]:
    """Legacy overview → default market (bookmark / old client compatible)."""
    state = app_state_from_request(request)
    return _list_pairs_body(state, runtime_or_404(state, None))


@router.get("/api/pairs/{pair_id}")
def get_pair(pair_id: str, request: Request) -> dict[str, Any]:
    """Legacy detail → default market."""
    state = app_state_from_request(request)
    return _get_pair_body(state, runtime_or_404(state, None), pair_id)


@router.get("/api/pairs/{pair_id}/mm")
def get_pair_mm(pair_id: str, request: Request) -> dict[str, Any]:
    """Legacy MM panel → default market."""
    state = app_state_from_request(request)
    return _get_pair_mm_body(state, runtime_or_404(state, None), pair_id)


@router.get("/api/pairs/{pair_id}/trades")
def get_pair_trades(pair_id: str, request: Request) -> dict[str, Any]:
    """Legacy trades → default market."""
    state = app_state_from_request(request)
    return _get_pair_trades_body(state, runtime_or_404(state, None), pair_id)


# --- Market-scoped routes ----------------------------------------------------


@router.get("/api/{market}/pairs")
def list_market_pairs(market: str, request: Request) -> dict[str, Any]:
    """Market-scoped overview table + PnL v2 + MM active badge."""
    state = app_state_from_request(request)
    return _list_pairs_body(state, runtime_or_404(state, market))


@router.get("/api/{market}/pairs/{pair_id}")
def get_market_pair(market: str, pair_id: str, request: Request) -> dict[str, Any]:
    """Market-scoped pair detail + PnL v2 + address panel."""
    state = app_state_from_request(request)
    return _get_pair_body(state, runtime_or_404(state, market), pair_id)


@router.get("/api/{market}/pairs/{pair_id}/mm")
def get_market_pair_mm(market: str, pair_id: str, request: Request) -> dict[str, Any]:
    """Market-scoped MM inventory curves + rebalance timeline."""
    state = app_state_from_request(request)
    return _get_pair_mm_body(state, runtime_or_404(state, market), pair_id)


@router.get("/api/{market}/pairs/{pair_id}/trades")
def get_market_pair_trades(
    market: str, pair_id: str, request: Request
) -> dict[str, Any]:
    """Market-scoped trade stream."""
    state = app_state_from_request(request)
    return _get_pair_trades_body(state, runtime_or_404(state, market), pair_id)
