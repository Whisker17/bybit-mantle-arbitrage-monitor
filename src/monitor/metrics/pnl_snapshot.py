"""Journal ticks → PnL v2 bucket tables / optimal summary (WHI-766).

Pure assembly over ``pnl_bucket_table`` / ``optimal_size``. No I/O.
Callers load ticks from ``JournalReader`` and apply optional TTL caching
(API layer — see ``monitor.api.pnl_cache``).

Depth: journal stores precomputed notional VWAP curves (``BybitDepthTick``),
not raw N-level books. We reconstruct stepwise levels so the pure engine's
base-sized VWAP walk and depth cap remain usable. Missing depth → L1 path
with ``has_depth=False`` (overview shows ``no_depth``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Literal

from monitor.fluxion.abi import USDC_DECIMALS
from monitor.metrics.amm_pool import AmmPoolState
from monitor.metrics.amm_quote import amm_quote_for_cex, is_tradable_amm_quote
from monitor.metrics.bybit_slip import BPS
from monitor.metrics.config import MetricsConfig
from monitor.metrics.edge import Direction, mid_from_bid_ask
from monitor.metrics.pnl_v2 import (
    DepthSource,
    OptimalSizeResult,
    PnlBucketTable,
    RfqPollQuote,
    pnl_bucket_table,
)
from monitor.quotes import (
    BybitBookTick,
    BybitDepthTick,
    FluxionPoolStateTick,
    FluxionRfqQuoteTick,
    rfq_side_leg,
)

PnlStatus = Literal[
    "ok",
    "no_book",
    "no_pool",
    "empty_pool",
    "invalid_mid",
    "pricing_anomaly",
    "no_depth",
    "no_fillable",
    # Kept for wire/UI compat. WHI-821 no longer early-returns this to wipe
    # tables — quiet CEX is annotated via quote_aged + *_quote_age_ms instead.
    "stale",
]

_DIRECTIONS: tuple[Direction, Direction] = (
    "buy_fluxion_sell_bybit",
    "buy_bybit_sell_fluxion",
)


@dataclass(frozen=True, slots=True)
class QuoteAges:
    """Per-leg recv ages for a PnL snapshot (WHI-821).

    Annotation only — never blanks tables. ``quote_aged`` is true when any
    computed age exceeds the caller's ``quote_max_age_ms``. Flattened onto
    the public JSON dataclasses so wire keys stay stable (``cex_quote_age_ms``).
    """

    cex_ms: int | None = None
    amm_ms: int | None = None
    depth_ms: int | None = None
    quote_aged: bool = False


@dataclass(frozen=True, slots=True)
class PnlOptimalSummary:
    """Compact optimal-size card for the overview table."""

    status: PnlStatus
    has_depth: bool
    direction: Direction | None = None
    optimal_notional_usd: Decimal | None = None
    optimal_net_pnl_usd: Decimal | None = None
    optimal_net_pnl_bps: Decimal | None = None
    bybit_depth_source: DepthSource | None = None
    # WHI-821: quiet event-driven CEX ≠ dead feed — annotate, don't blank.
    quote_aged: bool = False
    cex_quote_age_ms: int | None = None
    amm_quote_age_ms: int | None = None
    depth_quote_age_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        # Match serialize.to_jsonable / PnlResult.to_dict: fixed-point strings,
        # never scientific notation (format(..., "f")).
        def _dec(v: Decimal | None) -> str | None:
            return None if v is None else format(v, "f")

        return {
            "status": self.status,
            "has_depth": self.has_depth,
            "direction": self.direction,
            "optimal_notional_usd": _dec(self.optimal_notional_usd),
            "optimal_net_pnl_usd": _dec(self.optimal_net_pnl_usd),
            "optimal_net_pnl_bps": _dec(self.optimal_net_pnl_bps),
            "bybit_depth_source": self.bybit_depth_source,
            "quote_aged": self.quote_aged,
            "cex_quote_age_ms": self.cex_quote_age_ms,
            "amm_quote_age_ms": self.amm_quote_age_ms,
            "depth_quote_age_ms": self.depth_quote_age_ms,
        }

    def flat_sort_fields(self) -> tuple[Decimal | None, Decimal | None]:
        """USD / bps for overview sort keys (WHI-824).

        Only ``status == "ok"`` (including ``quote_aged`` ok) participates in
        numeric sort. Other statuses return ``(None, None)`` so callers park
        the row last and Top-N skips it. Does not special-case ``stale`` —
        when a row recovers to ok with numbers it sorts normally.
        """
        if self.status != "ok":
            return None, None
        return self.optimal_net_pnl_usd, self.optimal_net_pnl_bps


@dataclass(frozen=True, slots=True)
class PnlPairSnapshot:
    """Both-direction bucket tables + best optimal summary for one pair."""

    status: PnlStatus
    has_depth: bool
    best: PnlOptimalSummary
    tables: dict[Direction, PnlBucketTable]
    # WHI-821 per-leg recv ages (ms). None when now_ms not supplied.
    cex_quote_age_ms: int | None = None
    amm_quote_age_ms: int | None = None
    depth_quote_age_ms: int | None = None
    # True when any computed leg age exceeds quote_max_age_ms.
    quote_aged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "has_depth": self.has_depth,
            "best": self.best.to_dict(),
            "tables": {d: t.to_dict() for d, t in self.tables.items()},
            "cex_quote_age_ms": self.cex_quote_age_ms,
            "amm_quote_age_ms": self.amm_quote_age_ms,
            "depth_quote_age_ms": self.depth_quote_age_ms,
            "quote_aged": self.quote_aged,
        }


def levels_from_depth_curve(
    depth: BybitDepthTick,
    *,
    side: Literal["bid", "ask"],
) -> list[tuple[Decimal, Decimal]]:
    """Rebuild stepwise (price, size) levels from a precomputed notional VWAP curve.

    Journal buckets ``Q_i`` are cumulative USD notional; ``V_i`` is the notional
    VWAP after walking that notional (``spent/qty`` with ``spent == Q_i``). So
    cumulative base qty is ``Q_i / V_i``. Marginal reconstruction:

        cum_qty_i = Q_i / V_i
        d_qty = cum_qty_i − cum_qty_{i-1}
        d_notional = Q_i − Q_{i-1}
        P_i = d_notional / d_qty
        size_i = d_qty

    Round-trips through ``book_vwap_for_notional`` restore ``V_i`` (including
    sloped curves). Stops at the first unfillable rung (None VWAP).
    """
    curve = depth.bid_vwap_dm if side == "bid" else depth.ask_vwap_dm
    buckets = depth.buckets_usd
    if len(curve) != len(buckets):
        return []

    levels: list[tuple[Decimal, Decimal]] = []
    prev_q = Decimal(0)
    prev_qty = Decimal(0)
    for q, vwap in zip(buckets, curve, strict=True):
        if vwap is None or vwap <= 0 or q <= prev_q:
            break
        cum_qty = q / vwap
        d_qty = cum_qty - prev_qty
        d_notional = q - prev_q
        if d_qty <= 0 or d_notional <= 0:
            break
        px = d_notional / d_qty
        if px <= 0:
            break
        levels.append((px, d_qty))
        prev_q = q
        prev_qty = cum_qty
    return levels


def rfq_tick_to_poll_quote(
    tick: FluxionRfqQuoteTick,
    *,
    native_decimals: int,
    quote_decimals: int = USDC_DECIMALS,
) -> RfqPollQuote | None:
    """Convert a journal RFQ poll row (raw amounts) into engine human units.

    ``native_decimals`` comes from ``Pair.fluxion.native_decimals`` (config).
    """
    if not tick.available or tick.amount_out is None:
        return None
    leg = rfq_side_leg(tick.side)
    if leg is None:
        return None
    try:
        raw_in = Decimal(tick.amount_in)
        raw_out = Decimal(tick.amount_out)
    except (ArithmeticError, ValueError):
        return None
    if raw_in <= 0 or raw_out <= 0:
        return None
    if leg == "buy":
        # EXACT_INPUT USDC → base out
        amount_in = raw_in / (Decimal(10) ** quote_decimals)
        amount_out = raw_out / (Decimal(10) ** native_decimals)
    else:
        # EXACT_INPUT base → USDC out
        amount_in = raw_in / (Decimal(10) ** native_decimals)
        amount_out = raw_out / (Decimal(10) ** quote_decimals)
    if amount_in <= 0 or amount_out <= 0:
        return None
    return RfqPollQuote(amount_in=amount_in, amount_out=amount_out, fluxion_leg=leg)


def _empty_summary(
    *,
    status: PnlStatus,
    has_depth: bool,
    ages: QuoteAges | None = None,
) -> PnlOptimalSummary:
    a = ages or QuoteAges()
    return PnlOptimalSummary(
        status=status,
        has_depth=has_depth,
        quote_aged=a.quote_aged,
        cex_quote_age_ms=a.cex_ms,
        amm_quote_age_ms=a.amm_ms,
        depth_quote_age_ms=a.depth_ms,
    )


def _summary_from_optimal(
    opt: OptimalSizeResult,
    *,
    status: PnlStatus,
    has_depth: bool,
    ages: QuoteAges,
) -> PnlOptimalSummary:
    pnl_bps: Decimal | None = None
    if opt.q_star_usd > 0:
        pnl_bps = opt.pnl_usd / opt.q_star_usd * BPS
    return PnlOptimalSummary(
        status=status,
        has_depth=has_depth,
        direction=opt.direction,
        optimal_notional_usd=opt.q_star_usd,
        optimal_net_pnl_usd=opt.pnl_usd,
        optimal_net_pnl_bps=pnl_bps,
        bybit_depth_source=opt.result.bybit_depth_source,
        quote_aged=ages.quote_aged,
        cex_quote_age_ms=ages.cex_ms,
        amm_quote_age_ms=ages.amm_ms,
        depth_quote_age_ms=ages.depth_ms,
    )


def _quote_ages(
    *,
    bybit: BybitBookTick,
    amm: FluxionPoolStateTick,
    depth: BybitDepthTick | None,
    now_ms: int | None,
    quote_max_age_ms: int | None,
) -> QuoteAges:
    """Per-leg ages + aged flag.

    Ages are ``now − recv_ts`` when ``now_ms`` is set. ``quote_aged`` is true
    only when a computed age exceeds ``quote_max_age_ms`` — this annotates quiet
    event-driven books; it never blanks tables (WHI-821). Collector process
    liveness uses a separate ``collector_stale_ms`` on /api/health.
    """
    if now_ms is None:
        return QuoteAges()
    cex_age = now_ms - bybit.recv_ts_ms
    amm_age = now_ms - amm.recv_ts_ms
    depth_age = None if depth is None else now_ms - depth.recv_ts_ms
    if quote_max_age_ms is None or quote_max_age_ms <= 0:
        return QuoteAges(cex_ms=cex_age, amm_ms=amm_age, depth_ms=depth_age)
    aged = cex_age > quote_max_age_ms or amm_age > quote_max_age_ms
    if depth_age is not None and depth_age > quote_max_age_ms:
        aged = True
    return QuoteAges(
        cex_ms=cex_age,
        amm_ms=amm_age,
        depth_ms=depth_age,
        quote_aged=aged,
    )


def build_pnl_pair_snapshot(
    *,
    pair_id: str,
    bybit: BybitBookTick | None,
    amm: AmmPoolState | None,
    amm_tick: FluxionPoolStateTick | None,
    config: MetricsConfig,
    depth: BybitDepthTick | None = None,
    rfq_buy: FluxionRfqQuoteTick | None = None,
    rfq_sell: FluxionRfqQuoteTick | None = None,
    native_decimals: int = 18,
    rfq_enabled: bool = True,
    now_ms: int | None = None,
    quote_max_age_ms: int | None = None,
    include_optimal: bool = True,
) -> PnlPairSnapshot:
    """Build dual-direction PnL tables + best-of optimal summary.

    ``amm`` is the metrics pool geometry (caller builds via
    ``monitor.metrics.amm_pool.amm_pool_from_tick`` or the TUI wrapper).
    ``amm_tick`` is used for quotability and for per-leg age annotation.
    Overview consumers read ``.best``; detail consumers read ``.tables``.
    When depth is missing or reconstructs empty, tables still compute on L1
    but status is ``no_depth`` so the overview can render that label.

    Inventory-shape free: pass ``pair_id`` + optional RFQ ``native_decimals``.
    When ``rfq_enabled`` is False (AMM-only markets), RFQ poll rows are ignored.
    CEX bid/ask must already be in **comparable** space (journal
    ``*_de_multiplied`` — divide for Bybit, multiply for Binance BEP-677).

    Freshness (WHI-821): a quiet CEX book (event-driven bookTicker, no push
    while the price is flat) is **not** treated as missing data. Ages and
    ``quote_aged`` are annotations only; only true absence (no book / no pool)
    returns empty ``tables``. Process liveness is ``collector_stale_ms`` on
    health — never pass that threshold here as a wipe gate.
    """
    if bybit is None:
        empty = _empty_summary(status="no_book", has_depth=False)
        return PnlPairSnapshot(
            status="no_book", has_depth=False, best=empty, tables={}
        )
    if bybit.bid_de_multiplied <= 0 or bybit.ask_de_multiplied <= 0:
        empty = _empty_summary(status="no_book", has_depth=False)
        return PnlPairSnapshot(
            status="no_book", has_depth=False, best=empty, tables={}
        )

    if amm is None or amm_tick is None:
        empty = _empty_summary(status="no_pool", has_depth=False)
        return PnlPairSnapshot(
            status="no_pool", has_depth=False, best=empty, tables={}
        )

    # Same quotability reason as spreads (WHI-795 + WHI-822 magnitude guard).
    # Do not early-return tables={} — RFQ rows (bybit-fluxion) still compute;
    # AMM buckets land unfillable under empty_pool; pricing_anomaly strips
    # optimal claims below. Overview status prefers quote_reason over
    # no_fillable when optimal is empty.
    cex_mid = mid_from_bid_ask(bybit.bid_de_multiplied, bybit.ask_de_multiplied)
    _, quote_reason = amm_quote_for_cex(
        amm_tick,
        cex_mid=cex_mid,
        max_abs_spread_bps=config.max_abs_amm_spread_bps,
    )

    ages = _quote_ages(
        bybit=bybit,
        amm=amm_tick,
        depth=depth,
        now_ms=now_ms,
        quote_max_age_ms=quote_max_age_ms,
    )

    bybit_bids: list[tuple[Decimal, Decimal]] | None = None
    bybit_asks: list[tuple[Decimal, Decimal]] | None = None
    # Usable depth = reconstructed non-empty levels (not merely a journal row).
    has_depth = False
    if depth is not None:
        bids = levels_from_depth_curve(depth, side="bid")
        asks = levels_from_depth_curve(depth, side="ask")
        if bids or asks:
            has_depth = True
            bybit_bids = bids or None
            bybit_asks = asks or None

    rfq_quotes: list[RfqPollQuote] = []
    if rfq_enabled:
        for tick in (rfq_buy, rfq_sell):
            if tick is None:
                continue
            q = rfq_tick_to_poll_quote(tick, native_decimals=native_decimals)
            if q is not None:
                rfq_quotes.append(q)

    tables: dict[Direction, PnlBucketTable] = {}
    for direction in _DIRECTIONS:
        tables[direction] = pnl_bucket_table(
            pair_id=pair_id,
            bybit_bid=bybit.bid_de_multiplied,
            bybit_ask=bybit.ask_de_multiplied,
            direction=direction,
            config=config,
            amm=amm,
            bybit_bids=bybit_bids,
            bybit_asks=bybit_asks,
            rfq_quotes=rfq_quotes or None,
            include_optimal=include_optimal,
        )

    # Prefer the direction with higher fillable optimal PnL; ties → smaller Q.
    candidates: list[OptimalSizeResult] = []
    for table in tables.values():
        if table.optimal is not None and table.optimal.result.fillable:
            candidates.append(table.optimal)

    # Residual slot0 geometry / extreme basis must not win overview or detail
    # optimal — same seam as spreads (WHI-795 empty_pool, WHI-822 pricing_anomaly).
    if not is_tradable_amm_quote(quote_reason):
        candidates = []
        # Strip per-direction optimal so detail Bucket PnL cannot re-surface a
        # "Best optimal" / cost-at-Q* claim under a non-tradable reason.
        tables = {
            direction: replace(table, optimal=None)
            for direction, table in tables.items()
        }

    base_status: PnlStatus = "ok" if has_depth else "no_depth"

    def _snap(
        status: PnlStatus, best: PnlOptimalSummary
    ) -> PnlPairSnapshot:
        return PnlPairSnapshot(
            status=status,
            has_depth=has_depth,
            best=best,
            tables=tables,
            cex_quote_age_ms=ages.cex_ms,
            amm_quote_age_ms=ages.amm_ms,
            depth_quote_age_ms=ages.depth_ms,
            quote_aged=ages.quote_aged,
        )

    if not candidates:
        # Prefer unquotable reason (empty_pool / invalid_mid / pricing_anomaly)
        # over no_fillable so overview agrees with vs CEX. Prefer no_depth over
        # bare no_fillable.
        if quote_reason is not None:
            # AmmQuoteReason ⊆ PnlStatus (empty_pool / invalid_mid / pricing_anomaly).
            unfillable_status: PnlStatus = quote_reason
        elif not has_depth:
            unfillable_status = "no_depth"
        else:
            unfillable_status = "no_fillable"
        best = _empty_summary(
            status=unfillable_status, has_depth=has_depth, ages=ages
        )
        return _snap(unfillable_status, best)

    winner = max(candidates, key=lambda o: (o.pnl_usd, -o.q_star_usd))
    best = _summary_from_optimal(
        winner, status=base_status, has_depth=has_depth, ages=ages
    )
    return _snap(base_status, best)


def overview_pnl_summary(snapshot: PnlPairSnapshot) -> PnlOptimalSummary:
    """Overview column: force empty numbers when status is ``no_depth``.

    Detail still receives full L1 tables via ``snapshot.tables``; the overview
    cell must show the actionable "no depth" label, not an L1-only optimal.
    Age annotations from the snapshot are preserved either way (WHI-821).
    """
    if snapshot.status == "no_depth" or snapshot.best.status == "no_depth":
        return _empty_summary(
            status="no_depth",
            has_depth=False,
            ages=QuoteAges(
                cex_ms=snapshot.cex_quote_age_ms,
                amm_ms=snapshot.amm_quote_age_ms,
                depth_ms=snapshot.depth_quote_age_ms,
                quote_aged=snapshot.quote_aged,
            ),
        )
    return snapshot.best


__all__ = [
    "PnlOptimalSummary",
    "PnlPairSnapshot",
    "PnlStatus",
    "QuoteAges",
    "build_pnl_pair_snapshot",
    "levels_from_depth_curve",
    "overview_pnl_summary",
    "rfq_tick_to_poll_quote",
]
