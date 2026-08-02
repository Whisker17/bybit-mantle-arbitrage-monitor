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

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from monitor.fluxion.abi import NATIVE_DECIMALS_DEFAULT, USDC_DECIMALS
from monitor.metrics.amm_pool import AmmPoolState
from monitor.metrics.bybit_slip import BPS
from monitor.metrics.config import MetricsConfig
from monitor.metrics.edge import Direction
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
    "no_depth",
    "no_fillable",
    "stale",
]

_DIRECTIONS: tuple[Direction, Direction] = (
    "buy_fluxion_sell_bybit",
    "buy_bybit_sell_fluxion",
)


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "has_depth": self.has_depth,
            "direction": self.direction,
            "optimal_notional_usd": (
                None
                if self.optimal_notional_usd is None
                else format(self.optimal_notional_usd, "f")
            ),
            "optimal_net_pnl_usd": (
                None
                if self.optimal_net_pnl_usd is None
                else format(self.optimal_net_pnl_usd, "f")
            ),
            "optimal_net_pnl_bps": (
                None
                if self.optimal_net_pnl_bps is None
                else format(self.optimal_net_pnl_bps, "f")
            ),
            "bybit_depth_source": self.bybit_depth_source,
        }


@dataclass(frozen=True, slots=True)
class PnlPairSnapshot:
    """Both-direction bucket tables + best optimal summary for one pair."""

    status: PnlStatus
    has_depth: bool
    best: PnlOptimalSummary
    tables: dict[Direction, PnlBucketTable]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "has_depth": self.has_depth,
            "best": self.best.to_dict(),
            "tables": {d: t.to_dict() for d, t in self.tables.items()},
        }


def levels_from_depth_curve(
    depth: BybitDepthTick,
    *,
    side: Literal["bid", "ask"],
) -> list[tuple[Decimal, Decimal]]:
    """Rebuild stepwise (price, size) levels from a precomputed notional VWAP curve.

    For successive fillable buckets ``Q_i`` with VWAP ``V_i``:

        notional_i = Q_i * V_i  (total)
        marginal notional dN = Q_i*V_i − Q_{i-1}*V_{i-1}
        marginal price P_i = dN / dQ  where dQ = Q_i − Q_{i-1}
        size_i = dQ / P_i

    Stops at the first unfillable rung (None VWAP). Empty when no fillable rung.
    """
    curve = depth.bid_vwap_dm if side == "bid" else depth.ask_vwap_dm
    buckets = depth.buckets_usd
    if len(curve) != len(buckets):
        return []

    levels: list[tuple[Decimal, Decimal]] = []
    prev_q = Decimal(0)
    prev_notional = Decimal(0)
    for q, vwap in zip(buckets, curve, strict=True):
        if vwap is None or vwap <= 0 or q <= prev_q:
            break
        total_notional = q * vwap
        d_q = q - prev_q
        d_n = total_notional - prev_notional
        if d_q <= 0 or d_n <= 0:
            break
        px = d_n / d_q
        if px <= 0:
            break
        size = d_q / px
        if size <= 0:
            break
        levels.append((px, size))
        prev_q = q
        prev_notional = total_notional
    return levels


def rfq_tick_to_poll_quote(tick: FluxionRfqQuoteTick) -> RfqPollQuote | None:
    """Convert a journal RFQ poll row (raw amounts) into engine human units."""
    if not tick.available or tick.amount_out is None:
        return None
    leg = rfq_side_leg(tick.side)
    if leg is None:
        return None
    try:
        raw_in = Decimal(tick.amount_in)
        raw_out = Decimal(tick.amount_out)
    except Exception:  # noqa: BLE001 — malformed journal strings
        return None
    if raw_in <= 0 or raw_out <= 0:
        return None
    if leg == "buy":
        # EXACT_INPUT USDC → base out
        amount_in = raw_in / (Decimal(10) ** USDC_DECIMALS)
        amount_out = raw_out / (Decimal(10) ** NATIVE_DECIMALS_DEFAULT)
    else:
        # EXACT_INPUT base → USDC out
        amount_in = raw_in / (Decimal(10) ** NATIVE_DECIMALS_DEFAULT)
        amount_out = raw_out / (Decimal(10) ** USDC_DECIMALS)
    if amount_in <= 0 or amount_out <= 0:
        return None
    return RfqPollQuote(amount_in=amount_in, amount_out=amount_out, fluxion_leg=leg)


def _empty_summary(
    *,
    status: PnlStatus,
    has_depth: bool,
) -> PnlOptimalSummary:
    return PnlOptimalSummary(status=status, has_depth=has_depth)


def _summary_from_optimal(
    opt: OptimalSizeResult,
    *,
    status: PnlStatus,
    has_depth: bool,
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
    )


def _ticks_stale(
    *,
    bybit: BybitBookTick,
    amm: FluxionPoolStateTick,
    now_ms: int | None,
    stale_ms: int | None,
) -> bool:
    if now_ms is None or stale_ms is None or stale_ms <= 0:
        return False
    if now_ms - bybit.recv_ts_ms > stale_ms:
        return True
    if now_ms - amm.recv_ts_ms > stale_ms:
        return True
    return False


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
    now_ms: int | None = None,
    stale_ms: int | None = None,
    include_optimal: bool = True,
) -> PnlPairSnapshot:
    """Build dual-direction PnL tables + best-of optimal summary.

    ``amm`` is the metrics pool geometry (caller builds via ``amm_pool_from_tick``
    so this module never imports ``monitor.tui``). ``amm_tick`` is only used for
    freshness. Overview consumers read ``.best``; detail consumers read
    ``.tables``. When depth is missing, tables still compute on L1 but status
    is ``no_depth`` so the overview can render that label.
    """
    has_depth = depth is not None

    if bybit is None:
        empty = _empty_summary(status="no_book", has_depth=has_depth)
        return PnlPairSnapshot(
            status="no_book", has_depth=has_depth, best=empty, tables={}
        )
    if bybit.bid_de_multiplied <= 0 or bybit.ask_de_multiplied <= 0:
        empty = _empty_summary(status="no_book", has_depth=has_depth)
        return PnlPairSnapshot(
            status="no_book", has_depth=has_depth, best=empty, tables={}
        )

    if amm is None or amm_tick is None:
        empty = _empty_summary(status="no_pool", has_depth=has_depth)
        return PnlPairSnapshot(
            status="no_pool", has_depth=has_depth, best=empty, tables={}
        )

    if _ticks_stale(bybit=bybit, amm=amm_tick, now_ms=now_ms, stale_ms=stale_ms):
        empty = _empty_summary(status="stale", has_depth=has_depth)
        return PnlPairSnapshot(
            status="stale", has_depth=has_depth, best=empty, tables={}
        )

    bybit_bids: list[tuple[Decimal, Decimal]] | None = None
    bybit_asks: list[tuple[Decimal, Decimal]] | None = None
    if depth is not None:
        bids = levels_from_depth_curve(depth, side="bid")
        asks = levels_from_depth_curve(depth, side="ask")
        # Empty reconstructed curve → fall back to L1 (infinite) rather than
        # treating the book as zero-depth unfillable.
        bybit_bids = bids or None
        bybit_asks = asks or None

    rfq_quotes: list[RfqPollQuote] = []
    for tick in (rfq_buy, rfq_sell):
        if tick is None:
            continue
        q = rfq_tick_to_poll_quote(tick)
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

    base_status: PnlStatus = "ok" if has_depth else "no_depth"
    if not candidates:
        best = _empty_summary(status="no_fillable", has_depth=has_depth)
        # Prefer no_depth over no_fillable on the overview when depth missing
        # so the operator sees the actionable gap first.
        status: PnlStatus = "no_depth" if not has_depth else "no_fillable"
        if not has_depth:
            best = _empty_summary(status="no_depth", has_depth=False)
        return PnlPairSnapshot(
            status=status, has_depth=has_depth, best=best, tables=tables
        )

    winner = max(candidates, key=lambda o: (o.pnl_usd, -o.q_star_usd))
    best = _summary_from_optimal(winner, status=base_status, has_depth=has_depth)
    return PnlPairSnapshot(
        status=base_status, has_depth=has_depth, best=best, tables=tables
    )


def overview_pnl_summary(snapshot: PnlPairSnapshot) -> PnlOptimalSummary:
    """Overview column: hide optimal numbers when status is no_depth."""
    if snapshot.best.status == "no_depth" or snapshot.status == "no_depth":
        return _empty_summary(status="no_depth", has_depth=False)
    return snapshot.best


__all__ = [
    "PnlOptimalSummary",
    "PnlPairSnapshot",
    "PnlStatus",
    "build_pnl_pair_snapshot",
    "levels_from_depth_curve",
    "overview_pnl_summary",
    "rfq_tick_to_poll_quote",
]
