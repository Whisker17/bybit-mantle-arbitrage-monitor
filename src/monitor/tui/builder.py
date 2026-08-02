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
from functools import lru_cache
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
from monitor.fluxion.tvl import is_low_liquidity
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
from monitor.metrics.premium import (
    PremiumSnapshot,
    build_premium_snapshot,
    equity_equivalent_mid,
    mean_mid,
    premium_bps,
    reclassify_underlying_for_display,
)
from monitor.metrics.snapshot import rfq_price
from monitor.metrics.stats import Distribution
from monitor.metrics.volume import VolumeCompare, build_volume_compare
from monitor.quotes import (
    BybitBookTick,
    DexPoolTvlTick,
    FluxionPoolStateTick,
    FluxionRfqQuoteTick,
    FluxionSwapTick,
    UnderlyingPriceTick,
    now_ms,
    rfq_side_leg,
)
from monitor.storage import JournalReader
from monitor.symbols.bstocks_models import BStocksPair, BStocksPairsConfig
from monitor.symbols.models import Pair, PairsConfig
from monitor.tui.config import SortKey, TuiConfig
from monitor.tui.format import downsample, sort_rows
from monitor.tui.model import (
    EdgePanel,
    OverviewModel,
    PairDetailModel,
    PairOverviewRow,
    PremiumPanel,
    RunningEdgeState,
    SpreadPoint,
    TradeStreamRow,
)
from monitor.tui.pool import amm_pool_from_tick, quote_is_token0
from monitor.underlying.config import UnderlyingConfig, load_underlying_config
from monitor.underlying.tickers import (
    pair_id_to_underlying_ticker,
    underlying_tickers_for_pairs,
)

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


def _volume_fields(vol: VolumeCompare | None) -> dict[str, object]:
    """WHI-777 CEX/DEX columns for overview rows (defaults when unknown)."""
    if vol is None:
        return {
            "cex_volume_24h": None,
            "dex_volume_24h": Decimal(0),
            "volume_ratio": None,
            "dex_trade_count_24h": 0,
            "cex_trade_count_24h": None,
            "dex_volume_truncated": False,
            "dex_volume_window_start_ms": None,
        }
    return {
        "cex_volume_24h": vol.cex_volume_24h,
        "dex_volume_24h": vol.dex.volume_usd,
        "volume_ratio": vol.volume_ratio,
        "dex_trade_count_24h": vol.dex.trade_count,
        "cex_trade_count_24h": vol.cex_trade_count_24h,
        "dex_volume_truncated": vol.dex.truncated,
        "dex_volume_window_start_ms": vol.dex.window_start_ms,
    }


def _premium_fields(
    premium: PremiumSnapshot,
) -> dict[str, object]:
    """WHI-779 underlying + premium columns (explicit empty via snapshot)."""
    return {
        "underlying_ticker": premium.ticker,
        "underlying_price": premium.price,
        "underlying_currency": premium.currency,
        "underlying_price_type": premium.price_type,
        "underlying_as_of_ms": premium.as_of_ms,
        "underlying_source": premium.source,
        "underlying_empty": premium.empty_reason,
        "premium_bps": premium.premium_bps,
        "cex_premium_bps": premium.cex_premium_bps,
        "amm_premium_bps": premium.amm_premium_bps,
        "rfq_premium_bps": premium.rfq_premium_bps,
        "premium_type_label": premium.type_label,
    }


@lru_cache(maxsize=1)
def _underlying_cfg() -> UnderlyingConfig:
    """Startup-style load, cached for the process (fail-fast on bad YAML)."""
    return load_underlying_config()


def _private_tickers(cfg: UnderlyingConfig) -> frozenset[str]:
    return frozenset(cfg.uncovered_tickers())


def _ui_multiplier_for_pair(pair: Pair | BStocksPair) -> Decimal | None:
    """bStocks only: ui_multiplier so premium divides back to per-share units."""
    if isinstance(pair, BStocksPair):
        return pair.binance.ui_multiplier
    return None


def _reclassify_map(
    ticks: Mapping[str, UnderlyingPriceTick],
    *,
    now_ms: int,
    cfg: UnderlyingConfig,
) -> dict[str, UnderlyingPriceTick]:
    return {
        k: reclassify_underlying_for_display(
            t,
            now_ms=now_ms,
            session=cfg.session,
            stale_after_open_ms=cfg.stale_after_open_ms,
            stale_after_closed_ms=cfg.stale_after_closed_ms,
            stale_after_abs_ms=cfg.stale_after_abs_ms,
        )
        for k, t in ticks.items()
    }


def _premium_for_pair(
    pair: Pair | BStocksPair,
    *,
    bybit: BybitBookTick | None,
    amm: FluxionPoolStateTick | None,
    rfq_buy: FluxionRfqQuoteTick | None,
    rfq_sell: FluxionRfqQuoteTick | None,
    underlying_by_ticker: Mapping[str, UnderlyingPriceTick],
    private: frozenset[str],
) -> PremiumSnapshot:
    ticker = pair_id_to_underlying_ticker(pair.id)
    ui_mult = _ui_multiplier_for_pair(pair)
    cex_mid: Decimal | None = None
    if bybit is not None and bybit.bid_de_multiplied > 0 and bybit.ask_de_multiplied > 0:
        cex_mid = mid_from_bid_ask(bybit.bid_de_multiplied, bybit.ask_de_multiplied)
    cex_eq = equity_equivalent_mid(cex_mid, ui_multiplier=ui_mult)
    amm_raw = None if amm is None else amm.mid_usdc_per_native
    amm_eq = equity_equivalent_mid(amm_raw, ui_multiplier=ui_mult)
    # RFQ is Fluxion-only (no ui_multiplier path).
    rfq_mid = mean_mid(rfq_price(rfq_buy), rfq_price(rfq_sell))
    return build_premium_snapshot(
        ticker=ticker,
        underlying=underlying_by_ticker.get(ticker),
        cex_mid=cex_eq,
        amm_mid=amm_eq,
        rfq_mid=rfq_mid,
        private=ticker in private,
    )


def _inventory_quote_is_token0(pair: Pair | BStocksPair) -> bool:
    """Fallback when no pool tick: UniV3 address order → quote is token0."""
    if isinstance(pair, BStocksPair):
        quote = pair.pancake.quote_token_address.lower()
        base = pair.pancake.native_token.lower()
    else:
        quote = pair.fluxion.quote_token_address.lower()
        base = pair.fluxion.wrapper_token.lower()
    return quote < base


def _collector_coverage_ms(reader: JournalReader) -> int | None:
    """Write-once first start, falling back to this process start."""
    started_raw = reader.get_meta("collector_first_started_ms") or reader.get_meta(
        "collector_started_ms"
    )
    if started_raw is None:
        return None
    try:
        return int(started_raw)
    except ValueError:
        return None


def _quote_is_token0_resolved(
    pair: Pair | BStocksPair,
    *,
    reader: JournalReader,
    amm: FluxionPoolStateTick | None,
) -> bool:
    pool_tick = amm if amm is not None else reader.latest_pool_state(pair.id)
    q0 = quote_is_token0(pair, pool_tick) if pool_tick is not None else None
    return q0 if q0 is not None else _inventory_quote_is_token0(pair)


def _volume_compare_for_pair(
    pair: Pair | BStocksPair,
    *,
    reader: JournalReader,
    metrics: MetricsConfig,
    since_ms: int,
    now_ms: int,
    include_journal_cex: bool = False,
    amm: FluxionPoolStateTick | None = None,
    full_session_split: bool = False,
) -> VolumeCompare:
    """Assemble CEX REST vs DEX swap volume (WHI-777).

    Overview path (``full_session_split=False``) uses SQL totals for DEX so
    the 2s poll does not hydrate every swap row. Detail sets
    ``full_session_split=True`` for open/closed buckets.
    """
    cex = reader.latest_cex_volume(pair.id)
    earliest = reader.earliest_swap_recv_ts_ms(pair.id)
    collector_started = _collector_coverage_ms(reader)
    q0 = _quote_is_token0_resolved(pair, reader=reader, amm=amm)
    journal = (
        reader.trades_since(pair.id, since_ms=since_ms) if include_journal_cex else None
    )

    if full_session_split:
        swaps = reader.swaps_since(pair.id, since_ms=since_ms)
        return build_volume_compare(
            cex_tick=cex,
            swaps=swaps,
            quote_is_token0=q0,
            since_ms=since_ms,
            now_ms=now_ms,
            metrics=metrics,
            earliest_swap_recv_ts_ms=earliest,
            collector_started_ms=collector_started,
            journal_trades=journal,
        )

    # Lightweight overview: SQL sum/count + empty session buckets.
    from monitor.metrics.volume import (
        DexVolumeWindow,
        SessionVolumeSlice,
        VolumeCompare,
        aggregate_cex_journal_volume,
        volume_ratio,
    )

    notional, count = reader.dex_volume_totals(
        pair.id, since_ms=since_ms, quote_is_token0=q0
    )
    # Truncation metadata only (no swap list).
    coverage_candidates = [
        t for t in (collector_started, earliest) if t is not None
    ]
    coverage_start = min(coverage_candidates) if coverage_candidates else None
    truncated = coverage_start is not None and coverage_start > since_ms
    window_start = coverage_start if truncated else since_ms
    zero = SessionVolumeSlice(volume_usd=Decimal(0), trade_count=0)
    dex = DexVolumeWindow(
        volume_usd=notional,
        trade_count=count,
        open=zero,
        closed=zero,
        window_start_ms=window_start,
        requested_since_ms=since_ms,
        now_ms=now_ms,
        truncated=truncated,
        earliest_recv_ts_ms=earliest,
    )
    jwin = None
    if journal is not None:
        jwin = aggregate_cex_journal_volume(
            journal, since_ms=since_ms, now_ms=now_ms, metrics=metrics
        )
    cex_vol = None if cex is None else cex.volume_quote_24h
    cex_n = None if cex is None else cex.trade_count_24h
    if cex_n is None and jwin is not None:
        cex_n = jwin.trade_count
    return VolumeCompare(
        cex_volume_24h=cex_vol,
        cex_trade_count_24h=cex_n,
        cex_source=None if cex is None else cex.source,
        cex_poll_ts_ms=None if cex is None else cex.poll_ts_ms,
        dex=dex,
        cex_journal=jwin,
        volume_ratio=volume_ratio(cex_vol, notional),
    )


def _est_liquidity_usd(pair: Pair | BStocksPair) -> Decimal | None:
    """Inventory-time pool TVL estimate (not live journal).

    Fluxion pairs store it on ``fluxion.amm``; bStocks on ``pancake.amm``.
    None when the inventory has no AMM pool (still listed, low-liq / dex:none).
    """
    if isinstance(pair, BStocksPair):
        pancake_amm = pair.pancake.amm
        if pancake_amm is None:
            return None
        return Decimal(str(pancake_amm.est_liquidity_usd))
    fluxion_amm = pair.fluxion.amm
    if fluxion_amm is None:
        return None
    return Decimal(str(fluxion_amm.est_liquidity_usd))


def _pair_has_amm(pair: Pair | BStocksPair) -> bool:
    if isinstance(pair, BStocksPair):
        return pair.has_amm()
    return pair.fluxion.amm is not None


def _resolve_row_low_liquidity(
    pair: Pair | BStocksPair,
    *,
    tvl: DexPoolTvlTick | None,
    threshold_usd: Decimal,
) -> bool:
    """Live TVL when present; inventory flag as cold-start fallback (WHI-782)."""
    return is_low_liquidity(
        tvl_usd=None if tvl is None else tvl.tvl_usd,
        threshold_usd=threshold_usd,
        inventory_low=pair.low_liquidity,
        has_amm=_pair_has_amm(pair),
    )


def build_pair_overview_row(
    pair: Pair | BStocksPair,
    *,
    bybit: BybitBookTick | None,
    amm: FluxionPoolStateTick | None,
    rfq_buy: FluxionRfqQuoteTick | None,
    rfq_sell: FluxionRfqQuoteTick | None,
    volume_24h: Decimal,
    trades_24h: int,
    metrics: MetricsConfig,
    tui: TuiConfig,
    low_liquidity_threshold_usd: Decimal,
    ts_ms: int | None = None,
    volume_compare: VolumeCompare | None = None,
    premium: PremiumSnapshot | None = None,
    tvl: DexPoolTvlTick | None = None,
) -> PairOverviewRow:
    """Build one overview row. Pure: no I/O.

    Accepts Fluxion ``Pair`` or Pancake ``BStocksPair`` (M7); pool geometry
    is resolved via ``amm_pool_from_pair_tick``.
    ``low_liquidity_threshold_usd`` is required from inventory config (no
    hardcoded default — config/README.md).
    """
    ref = tui.reference_size_usd
    vfields = _volume_fields(volume_compare)
    prem = premium or PremiumSnapshot.empty(
        ticker=pair_id_to_underlying_ticker(pair.id), reason="no_data"
    )
    pfields = _premium_fields(prem)
    liq = _est_liquidity_usd(pair)
    low_liq = _resolve_row_low_liquidity(
        pair, tvl=tvl, threshold_usd=low_liquidity_threshold_usd
    )
    tvl_usd = None if tvl is None else tvl.tvl_usd
    tvl_as_of = None if tvl is None else tvl.recv_ts_ms
    if bybit is None:
        return PairOverviewRow(
            pair_id=pair.id,
            name=pair.name,
            low_liquidity=low_liq,
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
            est_liquidity_usd=liq,
            tvl_usd=tvl_usd,
            tvl_as_of_ms=tvl_as_of,
            **vfields,  # type: ignore[arg-type]
            **pfields,  # type: ignore[arg-type]
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
        low_liquidity=low_liq,
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
        est_liquidity_usd=liq,
        tvl_usd=tvl_usd,
        tvl_as_of_ms=tvl_as_of,
        **vfields,  # type: ignore[arg-type]
        **pfields,  # type: ignore[arg-type]
    )


def build_overview(
    *,
    pairs: PairsConfig | BStocksPairsConfig,
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
    Accepts Bybit/Fluxion or Binance/Pancake inventory roots.
    """
    ts = now if now is not None else now_ms()
    key = sort_key if sort_key is not None else tui.default_sort
    desc = tui.default_sort_desc if sort_desc is None else sort_desc
    since = ts - tui.volume_window_ms
    u_cfg = _underlying_cfg()
    private = _private_tickers(u_cfg)
    tickers = underlying_tickers_for_pairs([p.id for p in pairs.pairs])
    underlying_by = _reclassify_map(
        reader.latest_underlying_prices(tickers), now_ms=ts, cfg=u_cfg
    )
    threshold = Decimal(str(pairs.low_liquidity_threshold_usd))
    rows: list[PairOverviewRow] = []
    for pair in pairs.pairs:
        bybit = reader.latest_bybit_book(pair.id)
        amm = reader.latest_pool_state(pair.id)
        rfq_buy, rfq_sell = reader.latest_rfq_sides(pair.id)
        tvl = reader.latest_pool_tvl(pair.id)
        vol = reader.volume_stats(pair.id, since_ms=since)
        vcmp = _volume_compare_for_pair(
            pair,
            reader=reader,
            metrics=metrics,
            since_ms=since,
            now_ms=ts,
            amm=amm,
        )
        prem = _premium_for_pair(
            pair,
            bybit=bybit,
            amm=amm,
            rfq_buy=rfq_buy,
            rfq_sell=rfq_sell,
            underlying_by_ticker=underlying_by,
            private=private,
        )
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
                # TUI frozen: keep journal CEX notional for legacy Vol cell.
                volume_24h=vol.bybit_notional,
                trades_24h=vol.bybit_trade_count + vol.fluxion_swap_count,
                metrics=metrics,
                tui=tui,
                ts_ms=ts,
                volume_compare=vcmp,
                premium=prem,
                tvl=tvl,
                low_liquidity_threshold_usd=threshold,
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
    pair: Pair | BStocksPair,
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


def _venue_premiums_vs_underlying(
    *,
    cex_mid: Decimal | None,
    amm_mid: Decimal | None,
    rfq_buy_mid: Decimal | None,
    rfq_sell_mid: Decimal | None,
    underlying_price: Decimal | None,
    ui_multiplier: Decimal | None = None,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """CEX / AMM / RFQ equity-eq premiums vs one underlying print (WHI-783).

    Same rebasing rules as ``_premium_for_pair``: CEX and AMM go through
    ``equity_equivalent_mid``; RFQ is Fluxion-only (no ui_multiplier).
    Returns ``(cex, amm, rfq)`` bps, each None when undefined.
    """
    if underlying_price is None or underlying_price <= 0:
        return None, None, None
    u = underlying_price
    cex_eq = equity_equivalent_mid(cex_mid, ui_multiplier=ui_multiplier)
    amm_eq = equity_equivalent_mid(amm_mid, ui_multiplier=ui_multiplier)
    rfq_eq = mean_mid(rfq_buy_mid, rfq_sell_mid)
    return premium_bps(cex_eq, u), premium_bps(amm_eq, u), premium_bps(rfq_eq, u)


def build_spread_series(
    *,
    books: Sequence[BybitBookTick],
    pools: Sequence[FluxionPoolStateTick],
    metrics: MetricsConfig,
    max_points: int,
    rfq_quotes: Sequence[FluxionRfqQuoteTick] = (),
    underlying: Sequence[UnderlyingPriceTick] = (),
    ui_multiplier: Decimal | None = None,
) -> list[SpreadPoint]:
    """Join Bybit books to as-of AMM pool + RFQ quotes for the detail chart.

    RFQ is optional so older call sites still get AMM-only series; the Web
    detail page (WHI-759) passes journal RFQ history for the second line.
    Underlying prints (WHI-779) join as-of ``as_of_ms`` for premium series.
    ``ui_multiplier`` (bStocks) converts comparable CEX mid → equity units.
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
    und_by_ts = sorted(underlying, key=lambda u: u.as_of_ms)
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
        und = _as_of(und_by_ts, book.exchange_ts_ms, get_ts=lambda u: u.as_of_ms)
        cex_prem, amm_prem, rfq_prem = _venue_premiums_vs_underlying(
            cex_mid=snap.bybit_mid,
            amm_mid=snap.amm_mid,
            rfq_buy_mid=snap.rfq_buy_mid,
            rfq_sell_mid=snap.rfq_sell_mid,
            underlying_price=None if und is None else und.price,
            ui_multiplier=ui_multiplier,
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
                cex_premium_bps=cex_prem,
                amm_premium_bps=amm_prem,
                rfq_premium_bps=rfq_prem,
            )
        )
    if len(points) > max_points:
        # Even subsample (keep first/last); magnitude is unused by downsample.
        series = [(p.ts_ms, Decimal(0)) for p in points]
        kept_ts = {t for t, _ in downsample(series, max_points=max_points)}
        points = [p for p in points if p.ts_ms in kept_ts]
    return points


def _premium_distribution(
    points: Sequence[SpreadPoint],
) -> tuple[Distribution, Distribution, Distribution]:
    """Equal-weight CEX premium distributions over journal window."""
    all_v = [p.cex_premium_bps for p in points if p.cex_premium_bps is not None]
    open_v = [
        p.cex_premium_bps
        for p in points
        if p.cex_premium_bps is not None and p.session is SessionKind.OPEN
    ]
    closed_v = [
        p.cex_premium_bps
        for p in points
        if p.cex_premium_bps is not None and p.session is SessionKind.CLOSED
    ]
    return (
        Distribution.from_values(all_v),
        Distribution.from_values(open_v),
        Distribution.from_values(closed_v),
    )


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
    pair: Pair | BStocksPair,
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
    pair: Pair | BStocksPair,
    reader: JournalReader,
    metrics: MetricsConfig,
    attribution_cfg: AttributionConfig,
    tui: TuiConfig,
    edge_state: RunningEdgeState,
    now: int | None = None,
    cold_start: bool = False,
    low_liquidity_threshold_usd: Decimal,
) -> PairDetailModel:
    ts = now if now is not None else now_ms()
    since_vol = ts - tui.volume_window_ms
    bybit = reader.latest_bybit_book(pair.id)
    amm = reader.latest_pool_state(pair.id)
    rfq_buy, rfq_sell = reader.latest_rfq_sides(pair.id)
    tvl = reader.latest_pool_tvl(pair.id)
    vol = reader.volume_stats(pair.id, since_ms=since_vol)
    vcmp = _volume_compare_for_pair(
        pair,
        reader=reader,
        metrics=metrics,
        since_ms=since_vol,
        now_ms=ts,
        include_journal_cex=True,
        amm=amm,
        full_session_split=True,
    )
    u_cfg = _underlying_cfg()
    private = _private_tickers(u_cfg)
    ticker = pair_id_to_underlying_ticker(pair.id)
    underlying_by = _reclassify_map(
        reader.latest_underlying_prices([ticker]), now_ms=ts, cfg=u_cfg
    )
    prem = _premium_for_pair(
        pair,
        bybit=bybit,
        amm=amm,
        rfq_buy=rfq_buy,
        rfq_sell=rfq_sell,
        underlying_by_ticker=underlying_by,
        private=private,
    )
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
        volume_compare=vcmp,
        premium=prem,
        tvl=tvl,
        low_liquidity_threshold_usd=low_liquidity_threshold_usd,
    )
    # TVL history stays in the journal (reader.pool_tvl_series); not on the
    # hot detail path — overview already exposes latest tvl_usd / tvl_as_of_ms.

    books = reader.bybit_books(
        pair.id, limit=max(tui.spread_history_max_points, tui.edge_history_max_samples)
    )
    pools = reader.pool_states(pair.id, limit=tui.edge_history_max_samples)
    rfq_hist = reader.rfq_quotes(pair.id, limit=tui.edge_history_max_samples)
    und_hist = reader.underlying_prices(
        ticker, limit=tui.spread_history_max_points
    )

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
        underlying=und_hist,
        ui_multiplier=_ui_multiplier_for_pair(pair),
    )
    dist_all, dist_open, dist_closed = _premium_distribution(spread_series)
    premium_panel = PremiumPanel(
        current=prem,
        distribution=dist_all,
        distribution_open=dist_open,
        distribution_closed=dist_closed,
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
        low_liquidity=overview.low_liquidity,
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
        volume_compare=vcmp,
        premium=premium_panel,
    )
