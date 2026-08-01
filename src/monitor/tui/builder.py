"""Assemble overview / detail models from journal ticks via M3/M4 pure seams.

The TUI never reimplements spread, edge, or attribution math — it only joins
collector ticks and calls:

- ``build_spread_snapshot`` / ``build_edge_snapshot`` / ``best_net_edge``
- ``EdgeStats`` / ``SessionBuckets``
- ``amm_trade_from_swap`` / ``build_pair_attribution`` / ``is_converging``
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from monitor.attribution import (
    BehaviorLabel,
    build_pair_attribution,
    is_converging,
    mechanism_for_swap,
    resolve_bybit_mid_prev,
    rfq_fill_from_tick,
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
    best_net_edge,
    build_edge_snapshot,
    build_spread_snapshot,
    session_kind,
)
from monitor.metrics.edge import Direction, EdgeResult, VenueKind
from monitor.metrics.stats import BreachStats, Distribution
from monitor.quotes import (
    BybitBookTick,
    FluxionPoolStateTick,
    FluxionRfqQuoteTick,
    FluxionSwapTick,
    now_ms,
)
from monitor.symbols.models import Pair, PairsConfig
from monitor.tui.config import SortKey, TuiConfig
from monitor.tui.format import sort_rows
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
from monitor.tui.reader import JournalReader, downsample


def _session_at(ts_ms: int, metrics: MetricsConfig) -> SessionKind:
    return session_kind(datetime.fromtimestamp(ts_ms / 1000, tz=UTC), config=metrics)


def _pick_reference_edge(
    edges: Sequence[EdgeResult], *, size: Decimal
) -> EdgeResult | None:
    at_size = [e for e in edges if e.size_usd == size and e.fillable]
    if not at_size:
        at_size = [e for e in edges if e.size_usd == size]
    if not at_size:
        return None
    return max(at_size, key=lambda e: e.net_edge_bps)


def _rfq_spread_for_overview(
    buy: Decimal | None,
    sell: Decimal | None,
    buy_bps: Decimal | None,
    sell_bps: Decimal | None,
) -> Decimal | None:
    """Prefer the larger absolute RFQ side spread for the overview column."""
    candidates = [b for b in (buy_bps, sell_bps) if b is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda x: abs(x))


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
    combined = list(edge_snap.amm_edges) + list(edge_snap.rfq_edges)
    best = best_net_edge([e for e in combined if e.size_usd == ref] or list(combined))
    # Prefer reference-size fillable edge; fall back to best overall fillable.
    ref_best = _pick_reference_edge(combined, size=ref)
    chosen = ref_best if ref_best is not None else best

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
            spreads.rfq_buy_mid,
            spreads.rfq_sell_mid,
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
) -> OverviewModel:
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


def _empty_edge_panel() -> EdgePanel:
    empty_b = BreachStats(episode_count=0, total_duration_ms=0, currently_breaching=False)
    return EdgePanel(
        current=None,
        distribution_all=Distribution.empty(),
        distribution_open=Distribution.empty(),
        distribution_closed=Distribution.empty(),
        breach_all=empty_b,
        breach_open=empty_b,
        breach_closed=empty_b,
        costs=None,
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
    assert isinstance(st, EdgeStats)
    return st


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
    # Index pool / rfq by time for as-of joins.
    pool_by_ts = sorted(pools, key=lambda p: p.recv_ts_ms)
    rfq_buy_hist = [q for q in rfq_quotes if (q.side or "").lower() in ("buy_native", "buy")]
    rfq_sell_hist = [
        q for q in rfq_quotes if (q.side or "").lower() in ("sell_native", "sell")
    ]

    def asof_pool(ts: int) -> FluxionPoolStateTick | None:
        cur: FluxionPoolStateTick | None = None
        for p in pool_by_ts:
            if p.recv_ts_ms > ts:
                break
            cur = p
        return cur

    def asof_rfq(
        series: Sequence[FluxionRfqQuoteTick], ts: int
    ) -> FluxionRfqQuoteTick | None:
        cur: FluxionRfqQuoteTick | None = None
        for q in series:
            if q.poll_ts_ms > ts:
                break
            cur = q
        return cur

    # Downsample books for cost.
    if len(books) > tui.edge_history_max_samples:
        step = len(books) / tui.edge_history_max_samples
        books = [books[int(i * step)] for i in range(tui.edge_history_max_samples)]

    for book in books:
        if book.gap:
            continue
        amm = asof_pool(book.exchange_ts_ms)
        rfq_buy = asof_rfq(rfq_buy_hist, book.exchange_ts_ms)
        rfq_sell = asof_rfq(rfq_sell_hist, book.exchange_ts_ms)
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
) -> list[SpreadPoint]:
    pool_by_ts = sorted(pools, key=lambda p: p.recv_ts_ms)

    def asof(ts: int) -> FluxionPoolStateTick | None:
        cur: FluxionPoolStateTick | None = None
        for p in pool_by_ts:
            if p.recv_ts_ms > ts:
                break
            cur = p
        return cur

    points: list[SpreadPoint] = []
    for book in books:
        if book.bid_de_multiplied <= 0 or book.ask_de_multiplied <= 0:
            continue
        amm = asof(book.exchange_ts_ms)
        snap = build_spread_snapshot(
            bybit=book, amm=amm, config=metrics, ts_ms=book.exchange_ts_ms
        )
        points.append(
            SpreadPoint(
                ts_ms=book.exchange_ts_ms,
                amm_spread_bps=snap.amm_spread_bps,
                session=snap.session,
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


def _bybit_mid_at(
    series: Sequence[tuple[int, Decimal]], ts_ms: int
) -> Decimal | None:
    cur: Decimal | None = None
    for t, mid in series:
        if t > ts_ms:
            break
        cur = mid
    return cur


def _pool_mid_pre(
    pools: Sequence[FluxionPoolStateTick], ts_ms: int
) -> Decimal | None:
    """Latest pool mid with recv_ts_ms strictly before the trade."""
    cur: Decimal | None = None
    for p in pools:
        if p.recv_ts_ms >= ts_ms:
            break
        cur = p.mid_usdc_per_native
    return cur


def build_amm_trade_events(
    *,
    pair: Pair,
    swaps: Sequence[FluxionSwapTick],
    pools: Sequence[FluxionPoolStateTick],
    bybit_mids: Sequence[tuple[int, Decimal]],
    metrics: MetricsConfig,
    attribution: AttributionConfig,
) -> list[AmmTradeEvent]:
    pool_sorted = sorted(pools, key=lambda p: p.recv_ts_ms)
    mid_sorted = sorted(bybit_mids, key=lambda x: x[0])
    events: list[AmmTradeEvent] = []
    # Use latest pool tick for token order (stable per pair).
    latest_pool = pool_sorted[-1] if pool_sorted else None
    q0: bool | None = None
    if latest_pool is not None:
        q0 = quote_is_token0(pair, latest_pool)
    for swap in swaps:
        if q0 is None:
            # Infer from swap's pool tokens when possible via latest pool.
            notional = max(abs(swap.amount_token0), abs(swap.amount_token1))
        else:
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
    return events


def build_trade_stream(
    *,
    amm_events: Sequence[AmmTradeEvent],
    labels: Mapping[str, BehaviorLabel],
    rfq_fills: Sequence[RfqFillEvent],
    limit: int,
) -> list[TradeStreamRow]:
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
        rows.append(
            TradeStreamRow(
                ts_ms=ev.ts_ms,
                mechanism=mechanism_for_swap(ev).value,
                direction=ev.direction,
                notional_usd=ev.notional_usd,
                price=ev.fluxion_mid_pre,
                bybit_mid=ev.bybit_mid,
                converging=conv,
                taker=ev.taker,
                taker_label=None if lab is None else lab.value,
                tx_hash=ev.tx_hash,
            )
        )
    for fill in rfq_fills:
        rows.append(
            TradeStreamRow(
                ts_ms=fill.ts_ms,
                mechanism="rfq",
                direction="—",
                notional_usd=None,
                price=None,
                bybit_mid=None,
                converging=None,
                taker=None,
                taker_label="mm",
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

    if cold_start or not any(
        k[0] == pair.id for k in edge_state.stats
    ):
        rebuild_edge_history(
            edge_state,
            pair=pair,
            books=books,
            pools=pools,
            rfq_quotes=rfq_hist,
            metrics=metrics,
            tui=tui,
        )

    # Live sample from latest tick.
    if bybit is not None:
        pool = amm_pool_from_tick(pair, amm) if amm is not None else None
        snap = build_edge_snapshot(
            bybit=bybit,
            config=metrics,
            amm=amm,
            amm_pool=pool,
            rfq_buy=rfq_buy,
            rfq_sell=rfq_sell,
            ts_ms=ts,
        )
        observe_edges(
            edge_state,
            pair_id=pair.id,
            edges=list(snap.amm_edges) + list(snap.rfq_edges),
            ts_ms=ts,
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

    # Prefer the direction of the current best edge for the panel series.
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
    )

    swaps = reader.swaps(pair.id, limit=tui.trade_stream_limit * 2)
    mid_series = reader.bybit_mid_series(
        pair.id, limit=tui.edge_history_max_samples
    )
    amm_events = build_amm_trade_events(
        pair=pair,
        swaps=swaps,
        pools=pools,
        bybit_mids=mid_series,
        metrics=metrics,
        attribution=attribution_cfg,
    )
    rfq_fill_ticks = reader.rfq_fills(limit=tui.trade_stream_limit)
    rfq_events = [
        rfq_fill_from_tick(
            t,
            pair_id=None,  # unscoped until enrichment lands
            session=_session_at(t.recv_ts_ms, metrics),
        )
        for t in rfq_fill_ticks
    ]
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
