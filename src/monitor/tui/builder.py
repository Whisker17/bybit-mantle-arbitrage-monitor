"""Assemble overview / detail models from journal ticks via M3/M4 pure seams.

The TUI never reimplements spread, edge, or attribution math — it only joins
collector ticks and calls:

- ``build_spread_snapshot`` / ``build_edge_snapshot``
- ``EdgeStats`` / ``SessionBuckets``
- ``amm_trade_from_swap`` / ``build_pair_attribution`` / ``is_converging``
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypeVar

from monitor.attribution import (
    BehaviorLabel,
    build_pair_attribution,
    is_converging,
    mechanism_for_rfq_fill,
    mechanism_for_swap,
    resolve_bybit_mid_prev,
)
from monitor.attribution.config import AttributionConfig
from monitor.attribution.events import (
    AmmTradeEvent,
    RfqFillEvent,
    amm_trade_from_swap,
    swap_notional_usd,
)
from monitor.metrics import (
    EdgeStats,
    MetricsConfig,
    SessionBuckets,
    SessionKind,
    build_edge_snapshot,
    build_spread_snapshot,
    session_kind,
)
from monitor.metrics.edge import Direction, EdgeResult, VenueKind, mid_from_bid_ask
from monitor.quotes import (
    BybitBookTick,
    FluxionPoolStateTick,
    FluxionRfqQuoteTick,
    FluxionSwapTick,
    now_ms,
    rfq_side_leg,
)
from monitor.storage import JournalReader
from monitor.symbols.models import Pair, PairsConfig
from monitor.tui.config import SortKey, TuiConfig
from monitor.tui.format import downsample, sort_rows
from monitor.tui.model import (
    EdgePanel,
    OverviewModel,
    PairDetailModel,
    PairOverviewRow,
    RunningEdgeState,
    SpreadPoint,
    TradeStreamRow,
)
from monitor.tui.pool import amm_pool_from_tick, quote_is_token0

_T = TypeVar("_T")


def _session_at(ts_ms: int, metrics: MetricsConfig) -> SessionKind:
    return session_kind(datetime.fromtimestamp(ts_ms / 1000, tz=UTC), config=metrics)


def _pick_reference_edge(
    edges: Sequence[EdgeResult],
    *,
    size: Decimal,
    venues: frozenset[VenueKind] | None = None,
) -> EdgeResult | None:
    """Best *fillable* edge at the reference size; None if nothing fillable.

    Overview net-edge prefers AMM only: RFQ is polled at ~$100 (collector) while
    the ladder starts at $1K, and M3 RFQ slip is forced to 0 at every rung
    (DEFERRED_ISSUES / metrics.edge). Detail panels may pass venues=None.
    """
    at_size = [
        e
        for e in edges
        if e.size_usd == size
        and e.fillable
        and (venues is None or e.venue in venues)
    ]
    if not at_size:
        return None
    return max(at_size, key=lambda e: e.net_edge_bps)


def _rfq_spread_for_overview(
    buy_bps: Decimal | None,
    sell_bps: Decimal | None,
) -> Decimal | None:
    """Prefer the larger absolute RFQ side spread for the overview column."""
    candidates = [b for b in (buy_bps, sell_bps) if b is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda x: abs(x))


def _rfq_spread_for_series(
    buy_bps: Decimal | None,
    sell_bps: Decimal | None,
) -> Decimal | None:
    """Stable RFQ series point: mean of available sides (not max-abs).

    Overview uses max-abs so the table highlights the worse side. A chart line
    that flips legs each sample is misleading — average keeps the series on
    one continuous path when both quotes exist.
    """
    candidates = [b for b in (buy_bps, sell_bps) if b is not None]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return (candidates[0] + candidates[1]) / 2


def build_pair_overview_row(
    pair: Pair,
    *,
    bybit: BybitBookTick | None,
    amm: FluxionPoolStateTick | None,
    rfq_buy: FluxionRfqQuoteTick | None,
    rfq_sell: FluxionRfqQuoteTick | None,
    volume_24h: Decimal,
    trades_24h: int,
    metrics: MetricsConfig,
    tui: TuiConfig,
    ts_ms: int | None = None,
) -> PairOverviewRow:
    """Build one overview row. Pure: no I/O."""
    ref = tui.reference_size_usd
    if bybit is None:
        return PairOverviewRow(
            pair_id=pair.id,
            name=pair.name,
            low_liquidity=pair.low_liquidity,
            session=None,
            bybit_bid=None,
            bybit_ask=None,
            bybit_mid=None,
            amm_mid=None if amm is None else amm.mid_usdc_per_native,
            rfq_buy=None if rfq_buy is None or not rfq_buy.available else rfq_buy.price,
            rfq_sell=(
                None if rfq_sell is None or not rfq_sell.available else rfq_sell.price
            ),
            amm_spread_bps=None,
            rfq_spread_bps=None,
            net_edge_bps=None,
            net_edge_venue=None,
            net_edge_direction=None,
            reference_size_usd=ref,
            volume_24h=volume_24h,
            trades_24h=trades_24h,
            stale=True,
        )

    pool = amm_pool_from_tick(pair, amm) if amm is not None else None
    edge_snap = build_edge_snapshot(
        bybit=bybit,
        config=metrics,
        amm=amm,
        amm_pool=pool,
        rfq_buy=rfq_buy,
        rfq_sell=rfq_sell,
        ts_ms=ts_ms,
    )
    spreads = edge_snap.spreads
    # Overview Net column: AMM-only at reference size (see _pick_reference_edge).
    chosen = _pick_reference_edge(edge_snap.amm_edges, size=ref, venues=frozenset({"amm"}))

    return PairOverviewRow(
        pair_id=pair.id,
        name=pair.name,
        low_liquidity=pair.low_liquidity,
        session=spreads.session,
        bybit_bid=bybit.bid_de_multiplied,
        bybit_ask=bybit.ask_de_multiplied,
        bybit_mid=spreads.bybit_mid,
        amm_mid=spreads.amm_mid,
        rfq_buy=spreads.rfq_buy_mid,
        rfq_sell=spreads.rfq_sell_mid,
        amm_spread_bps=spreads.amm_spread_bps,
        rfq_spread_bps=_rfq_spread_for_overview(
            spreads.rfq_buy_spread_bps,
            spreads.rfq_sell_spread_bps,
        ),
        net_edge_bps=None if chosen is None else chosen.net_edge_bps,
        net_edge_venue=None if chosen is None else chosen.venue,
        net_edge_direction=None if chosen is None else chosen.direction,
        reference_size_usd=ref,
        volume_24h=volume_24h,
        trades_24h=trades_24h,
        stale=False,
    )


def build_overview(
    *,
    pairs: PairsConfig,
    reader: JournalReader,
    metrics: MetricsConfig,
    tui: TuiConfig,
    sort_key: SortKey | None = None,
    sort_desc: bool | None = None,
    now: int | None = None,
    edge_state: RunningEdgeState | None = None,
) -> OverviewModel:
    """Build the overview table; optionally feed running EdgeStats from latest ticks.

    Passing ``edge_state`` keeps cumulative series warm while the operator stays
    on the overview page (detail cold-start then has continuous history).
    """
    ts = now if now is not None else now_ms()
    key = sort_key if sort_key is not None else tui.default_sort
    desc = tui.default_sort_desc if sort_desc is None else sort_desc
    since = ts - tui.volume_window_ms
    rows: list[PairOverviewRow] = []
    for pair in pairs.pairs:
        bybit = reader.latest_bybit_book(pair.id)
        amm = reader.latest_pool_state(pair.id)
        rfq_buy, rfq_sell = reader.latest_rfq_sides(pair.id)
        vol = reader.volume_stats(pair.id, since_ms=since)
        if edge_state is not None and bybit is not None:
            # Stamp with exchange time so a stalled collector does not inflate
            # EdgeStats with synthetic 1.5s samples of the same book.
            sample_ts = bybit.exchange_ts_ms
            pool = amm_pool_from_tick(pair, amm) if amm is not None else None
            snap = build_edge_snapshot(
                bybit=bybit,
                config=metrics,
                amm=amm,
                amm_pool=pool,
                rfq_buy=rfq_buy,
                rfq_sell=rfq_sell,
                ts_ms=sample_ts,
            )
            observe_edges(
                edge_state,
                pair_id=pair.id,
                edges=list(snap.amm_edges) + list(snap.rfq_edges),
                ts_ms=sample_ts,
                session=snap.spreads.session,
                metrics=metrics,
                reference_size=tui.reference_size_usd,
            )
        rows.append(
            build_pair_overview_row(
                pair,
                bybit=bybit,
                amm=amm,
                rfq_buy=rfq_buy,
                rfq_sell=rfq_sell,
                volume_24h=vol.bybit_notional,
                trades_24h=vol.bybit_trade_count + vol.fluxion_swap_count,
                metrics=metrics,
                tui=tui,
                ts_ms=ts,
            )
        )
    rows = sort_rows(rows, key=key, desc=desc)
    return OverviewModel(
        generated_ts_ms=ts,
        session_now=_session_at(ts, metrics),
        sort_key=key,
        sort_desc=desc,
        reference_size_usd=tui.reference_size_usd,
        rows=rows,
        db_path=str(reader.path),
    )


def _panel_from_stats(
    stats: EdgeStats, current: EdgeResult | None
) -> EdgePanel:
    buckets = SessionBuckets.from_stats(stats)
    return EdgePanel(
        current=current,
        distribution_all=buckets.all,
        distribution_open=buckets.open,
        distribution_closed=buckets.closed,
        breach_all=buckets.breach_all,
        breach_open=buckets.breach_open,
        breach_closed=buckets.breach_closed,
        costs=None if current is None else current.costs,
    )


def _ensure_stats(
    state: RunningEdgeState,
    *,
    pair_id: str,
    venue: VenueKind,
    direction: Direction,
    metrics: MetricsConfig,
) -> EdgeStats:
    key = (pair_id, venue, direction)
    st = state.stats.get(key)
    if st is None:
        st = EdgeStats.from_config(metrics)
        state.stats[key] = st
    return st


def _as_of(
    items: Sequence[_T],
    ts_ms: int,
    *,
    get_ts: Callable[[_T], int],
) -> _T | None:
    """Latest item with get_ts(item) <= ts_ms (items sorted ascending by that key)."""
    cur: _T | None = None
    for item in items:
        if get_ts(item) > ts_ms:
            break
        cur = item
    return cur


def observe_edges(
    state: RunningEdgeState,
    *,
    pair_id: str,
    edges: Sequence[EdgeResult],
    ts_ms: int,
    session: SessionKind,
    metrics: MetricsConfig,
    reference_size: Decimal,
) -> None:
    """Feed new edge samples into running stats (idempotent on same ts)."""
    for edge in edges:
        if edge.size_usd != reference_size:
            continue
        key = (pair_id, edge.venue, edge.direction)
        last = state.last_sample_ts.get(key)
        if last is not None and ts_ms <= last:
            continue
        stats = _ensure_stats(
            state,
            pair_id=pair_id,
            venue=edge.venue,
            direction=edge.direction,
            metrics=metrics,
        )
        stats.observe_edge(edge, ts_ms=ts_ms, session=session)
        state.last_sample_ts[key] = ts_ms


def rebuild_edge_history(
    state: RunningEdgeState,
    *,
    pair: Pair,
    books: Sequence[BybitBookTick],
    pools: Sequence[FluxionPoolStateTick],
    rfq_quotes: Sequence[FluxionRfqQuoteTick],
    metrics: MetricsConfig,
    tui: TuiConfig,
) -> None:
    """Cold-start: walk historical ticks into EdgeStats (once per series)."""
    pool_by_ts = sorted(pools, key=lambda p: p.recv_ts_ms)
    # Side vocabulary comes from monitor.quotes so reader / metrics / TUI agree.
    rfq_buy_hist = sorted(
        [q for q in rfq_quotes if rfq_side_leg(q.side) == "buy"],
        key=lambda q: q.poll_ts_ms,
    )
    rfq_sell_hist = sorted(
        [q for q in rfq_quotes if rfq_side_leg(q.side) == "sell"],
        key=lambda q: q.poll_ts_ms,
    )

    # Downsample books for cost.
    if len(books) > tui.edge_history_max_samples:
        step = len(books) / tui.edge_history_max_samples
        books = [books[int(i * step)] for i in range(tui.edge_history_max_samples)]

    for book in books:
        if book.gap:
            continue
        amm = _as_of(pool_by_ts, book.exchange_ts_ms, get_ts=lambda p: p.recv_ts_ms)
        rfq_buy = _as_of(
            rfq_buy_hist, book.exchange_ts_ms, get_ts=lambda q: q.poll_ts_ms
        )
        rfq_sell = _as_of(
            rfq_sell_hist, book.exchange_ts_ms, get_ts=lambda q: q.poll_ts_ms
        )
        pool = amm_pool_from_tick(pair, amm) if amm is not None else None
        snap = build_edge_snapshot(
            bybit=book,
            config=metrics,
            amm=amm,
            amm_pool=pool,
            rfq_buy=rfq_buy,
            rfq_sell=rfq_sell,
            ts_ms=book.exchange_ts_ms,
        )
        observe_edges(
            state,
            pair_id=pair.id,
            edges=list(snap.amm_edges) + list(snap.rfq_edges),
            ts_ms=book.exchange_ts_ms,
            session=snap.spreads.session,
            metrics=metrics,
            reference_size=tui.reference_size_usd,
        )


def build_spread_series(
    *,
    books: Sequence[BybitBookTick],
    pools: Sequence[FluxionPoolStateTick],
    metrics: MetricsConfig,
    max_points: int,
    rfq_quotes: Sequence[FluxionRfqQuoteTick] = (),
) -> list[SpreadPoint]:
    """Join Bybit books to as-of AMM pool + RFQ quotes for the detail chart.

    RFQ is optional so older call sites still get AMM-only series; the Web
    detail page (WHI-759) passes journal RFQ history for the second line.
    """
    pool_by_ts = sorted(pools, key=lambda p: p.recv_ts_ms)
    rfq_buy_hist = sorted(
        [q for q in rfq_quotes if rfq_side_leg(q.side) == "buy"],
        key=lambda q: q.poll_ts_ms,
    )
    rfq_sell_hist = sorted(
        [q for q in rfq_quotes if rfq_side_leg(q.side) == "sell"],
        key=lambda q: q.poll_ts_ms,
    )
    points: list[SpreadPoint] = []
    for book in books:
        if book.bid_de_multiplied <= 0 or book.ask_de_multiplied <= 0:
            continue
        amm = _as_of(pool_by_ts, book.exchange_ts_ms, get_ts=lambda p: p.recv_ts_ms)
        rfq_buy = _as_of(
            rfq_buy_hist, book.exchange_ts_ms, get_ts=lambda q: q.poll_ts_ms
        )
        rfq_sell = _as_of(
            rfq_sell_hist, book.exchange_ts_ms, get_ts=lambda q: q.poll_ts_ms
        )
        snap = build_spread_snapshot(
            bybit=book,
            amm=amm,
            config=metrics,
            rfq_buy=rfq_buy,
            rfq_sell=rfq_sell,
            ts_ms=book.exchange_ts_ms,
        )
        points.append(
            SpreadPoint(
                ts_ms=book.exchange_ts_ms,
                amm_spread_bps=snap.amm_spread_bps,
                session=snap.session,
                rfq_spread_bps=_rfq_spread_for_series(
                    snap.rfq_buy_spread_bps,
                    snap.rfq_sell_spread_bps,
                ),
                bybit_mid=snap.bybit_mid,
            )
        )
    if len(points) > max_points:
        # Reuse downsample on (ts, spread) then reattach session via index.
        series = [
            (p.ts_ms, p.amm_spread_bps if p.amm_spread_bps is not None else Decimal(0))
            for p in points
        ]
        kept_ts = {t for t, _ in downsample(series, max_points=max_points)}
        points = [p for p in points if p.ts_ms in kept_ts]
    return points


def bybit_mid_series(
    books: Sequence[BybitBookTick],
) -> list[tuple[int, Decimal]]:
    """(exchange_ts_ms, mid) ascending for lead-lag joins, via the M3 mid formula.

    Derived here rather than in ``monitor.storage`` so the journal reader stays
    free of metrics math.
    """
    return [
        (b.exchange_ts_ms, mid_from_bid_ask(b.bid_de_multiplied, b.ask_de_multiplied))
        for b in books
        if b.bid_de_multiplied > 0 and b.ask_de_multiplied > 0
    ]


def _bybit_mid_at(
    series: Sequence[tuple[int, Decimal]], ts_ms: int
) -> Decimal | None:
    hit = _as_of(series, ts_ms, get_ts=lambda p: p[0])
    return None if hit is None else hit[1]


def _pool_mid_pre(
    pools: Sequence[FluxionPoolStateTick], ts_ms: int
) -> Decimal | None:
    """Latest pool mid with recv_ts_ms strictly before the trade."""
    # Strictly before: use ts_ms - 1 as the inclusive ceiling.
    hit = _as_of(pools, ts_ms - 1, get_ts=lambda p: p.recv_ts_ms)
    return None if hit is None else hit.mid_usdc_per_native


def build_amm_trade_events(
    *,
    pair: Pair,
    swaps: Sequence[FluxionSwapTick],
    pools: Sequence[FluxionPoolStateTick],
    bybit_mids: Sequence[tuple[int, Decimal]],
    metrics: MetricsConfig,
    attribution: AttributionConfig,
) -> tuple[list[AmmTradeEvent], dict[tuple[str, int], Decimal | None]]:
    """Return (events, fill_price_by_tx_log) for the trade stream.

    Fill price is the swap's ``price_usdc_per_wrapper`` (execution print), not
    the pre-trade mid used for convergence.
    """
    pool_sorted = sorted(pools, key=lambda p: p.recv_ts_ms)
    mid_sorted = sorted(bybit_mids, key=lambda x: x[0])
    events: list[AmmTradeEvent] = []
    fill_px: dict[tuple[str, int], Decimal | None] = {}
    latest_pool = pool_sorted[-1] if pool_sorted else None
    q0: bool | None = None
    if latest_pool is not None:
        q0 = quote_is_token0(pair, latest_pool)
    for swap in swaps:
        if q0 is None:
            # Cannot size the USDC leg without token order — skip rather than
            # invent a max(|amt0|,|amt1|) pseudo-USD notional.
            continue
        notional = swap_notional_usd(swap, quote_is_token0=q0)
        ts = swap.recv_ts_ms
        bybit_mid = _bybit_mid_at(mid_sorted, ts)
        flux_pre = _pool_mid_pre(pool_sorted, ts)
        prev = resolve_bybit_mid_prev(
            ts,
            mid_sorted,
            lookback_ms=attribution.bybit_correlation.lookback_ms,
        )
        ev = amm_trade_from_swap(
            swap,
            notional_usd=notional,
            fluxion_mid_pre=flux_pre,
            bybit_mid=bybit_mid,
            bybit_mid_prev=prev,
            session=_session_at(ts, metrics),
            ts_ms=ts,
        )
        if ev is not None:
            events.append(ev)
            fill_px[(swap.tx_hash.lower(), swap.log_index)] = (
                swap.price_usdc_per_wrapper
            )
    return events, fill_px


def build_trade_stream(
    *,
    amm_events: Sequence[AmmTradeEvent],
    labels: Mapping[str, BehaviorLabel],
    fill_prices: Mapping[tuple[str, int], Decimal | None],
    rfq_fills: Sequence[RfqFillEvent] = (),
    limit: int,
) -> list[TradeStreamRow]:
    """Build the detail-page trade scroll (AMM fills + pair-scoped RFQ only)."""
    rows: list[TradeStreamRow] = []
    for ev in amm_events:
        conv = None
        if ev.fluxion_mid_pre is not None and ev.bybit_mid is not None:
            conv = is_converging(
                ev.direction,
                fluxion_mid=ev.fluxion_mid_pre,
                bybit_mid=ev.bybit_mid,
            )
        lab = labels.get(ev.taker.lower())
        px = fill_prices.get((ev.tx_hash.lower(), ev.log_index))
        rows.append(
            TradeStreamRow(
                ts_ms=ev.ts_ms,
                mechanism=mechanism_for_swap(ev).value,
                direction=ev.direction,
                notional_usd=ev.notional_usd,
                price=px,
                bybit_mid=ev.bybit_mid,
                converging=conv,
                taker=ev.taker,
                taker_label=None if lab is None else lab.value,
                tx_hash=ev.tx_hash,
            )
        )
    for fill in rfq_fills:
        if fill.pair_id is None:
            continue
        rows.append(
            TradeStreamRow(
                ts_ms=fill.ts_ms,
                mechanism=mechanism_for_rfq_fill(fill).value,
                direction="unknown",
                notional_usd=None,
                price=None,
                bybit_mid=None,
                converging=None,
                taker=None,
                taker_label=None,
                tx_hash=fill.tx_hash,
            )
        )
    rows.sort(key=lambda r: r.ts_ms, reverse=True)
    return rows[:limit]


def build_pair_detail(
    *,
    pair: Pair,
    reader: JournalReader,
    metrics: MetricsConfig,
    attribution_cfg: AttributionConfig,
    tui: TuiConfig,
    edge_state: RunningEdgeState,
    now: int | None = None,
    cold_start: bool = False,
) -> PairDetailModel:
    ts = now if now is not None else now_ms()
    since_vol = ts - tui.volume_window_ms
    bybit = reader.latest_bybit_book(pair.id)
    amm = reader.latest_pool_state(pair.id)
    rfq_buy, rfq_sell = reader.latest_rfq_sides(pair.id)
    vol = reader.volume_stats(pair.id, since_ms=since_vol)
    overview = build_pair_overview_row(
        pair,
        bybit=bybit,
        amm=amm,
        rfq_buy=rfq_buy,
        rfq_sell=rfq_sell,
        volume_24h=vol.bybit_notional,
        trades_24h=vol.bybit_trade_count + vol.fluxion_swap_count,
        metrics=metrics,
        tui=tui,
        ts_ms=ts,
    )

    books = reader.bybit_books(
        pair.id, limit=max(tui.spread_history_max_points, tui.edge_history_max_samples)
    )
    pools = reader.pool_states(pair.id, limit=tui.edge_history_max_samples)
    rfq_hist = reader.rfq_quotes(pair.id, limit=tui.edge_history_max_samples)

    # Overview live ticks may have already seeded EdgeStats keys; that must
    # not skip a full journal walk. Rebuild once per pair per process.
    if cold_start or pair.id not in edge_state.history_rebuilt:
        for key in list(edge_state.stats):
            if key[0] == pair.id:
                del edge_state.stats[key]
                edge_state.last_sample_ts.pop(key, None)
        rebuild_edge_history(
            edge_state,
            pair=pair,
            books=books,
            pools=pools,
            rfq_quotes=rfq_hist,
            metrics=metrics,
            tui=tui,
        )
        edge_state.history_rebuilt.add(pair.id)

    # Live sample from latest tick (exchange time, not wall clock).
    if bybit is not None:
        sample_ts = bybit.exchange_ts_ms
        pool = amm_pool_from_tick(pair, amm) if amm is not None else None
        snap = build_edge_snapshot(
            bybit=bybit,
            config=metrics,
            amm=amm,
            amm_pool=pool,
            rfq_buy=rfq_buy,
            rfq_sell=rfq_sell,
            ts_ms=sample_ts,
        )
        observe_edges(
            edge_state,
            pair_id=pair.id,
            edges=list(snap.amm_edges) + list(snap.rfq_edges),
            ts_ms=sample_ts,
            session=snap.spreads.session,
            metrics=metrics,
            reference_size=tui.reference_size_usd,
        )
        amm_edges = snap.amm_edges
        rfq_edges = snap.rfq_edges
    else:
        amm_edges = []
        rfq_edges = []

    ref = tui.reference_size_usd
    cur_amm = _pick_reference_edge(amm_edges, size=ref)
    cur_rfq = _pick_reference_edge(rfq_edges, size=ref)

    def stats_for(venue: VenueKind, direction: Direction) -> EdgeStats:
        return _ensure_stats(
            edge_state,
            pair_id=pair.id,
            venue=venue,
            direction=direction,
            metrics=metrics,
        )

    # Lock the cumulative series to the direction with the best *current*
    # fillable edge when present; otherwise keep a stable default so the
    # panel does not jump between EdgeStats series on every tick.
    amm_dir: Direction = (
        cur_amm.direction if cur_amm is not None else "buy_fluxion_sell_bybit"
    )
    rfq_dir: Direction = (
        cur_rfq.direction if cur_rfq is not None else "buy_fluxion_sell_bybit"
    )
    edge_amm = _panel_from_stats(stats_for("amm", amm_dir), cur_amm)
    edge_rfq = _panel_from_stats(stats_for("rfq", rfq_dir), cur_rfq)

    spread_series = build_spread_series(
        books=books[-tui.spread_history_max_points :],
        pools=pools,
        metrics=metrics,
        max_points=tui.spread_history_max_points,
        rfq_quotes=rfq_hist,
    )

    swaps = reader.swaps(pair.id, limit=tui.trade_stream_limit * 2)
    # Reuse the books already read above instead of a second query.
    mid_series = bybit_mid_series(books)
    amm_events, fill_prices = build_amm_trade_events(
        pair=pair,
        swaps=swaps,
        pools=pools,
        bybit_mids=mid_series,
        metrics=metrics,
        attribution=attribution_cfg,
    )
    # RFQ fills lack pair_id in storage (DEFERRED_ISSUES) — do not invent one.
    rfq_events: list[RfqFillEvent] = []
    attr = build_pair_attribution(
        pair_id=pair.id,
        amm_trades=amm_events,
        rfq_fills=rfq_events,
        config=attribution_cfg,
        session="all",
    )
    labels = {t.address.lower(): t.label for t in attr.takers}
    trades = build_trade_stream(
        amm_events=amm_events,
        labels=labels,
        fill_prices=fill_prices,
        rfq_fills=rfq_events,
        limit=tui.trade_stream_limit,
    )

    return PairDetailModel(
        pair_id=pair.id,
        name=pair.name,
        low_liquidity=pair.low_liquidity,
        generated_ts_ms=ts,
        session_now=_session_at(ts, metrics),
        overview=overview,
        spread_series=spread_series,
        trades=trades,
        edge_amm=edge_amm,
        edge_rfq=edge_rfq,
        attribution=attr,
        arb_bot_trade_share=attr.label_trade_share.get(BehaviorLabel.ARB_BOT),
        price_keeper_trade_share=attr.label_trade_share.get(
            BehaviorLabel.PRICE_KEEPER
        ),
        rfq_mechanism_share=attr.mechanism.rfq_share,
    )
