"""Occupancy-bounded capture rate (WHI-963).

Qualifying poll cycles are not opportunities. The executing bot is
single-flight with ~6–7 min occupancy per cycle, so capture is bounded by
window count × occupancy, not by how often Net flashes green.

Pure math reuses ``monitor.analysis.edge_quant`` (detect_windows +
capturable_profit_single_flight). Sample construction from journal ticks
lives here so live API and offline scripts share one path.

Config defaults track the sibling bot (``cycle_duration_s`` 390,
``reentry_cooldown_s`` 420) — see bot ``docs/DESIGN.md`` §2.5 / §2.8.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from monitor.analysis.edge_quant import (
    EdgeSample,
    OpportunityWindow,
    as_of_value,
    capturable_profit_single_flight,
    detect_windows,
)
from monitor.metrics.amm_pool import amm_pool_from_pair_tick
from monitor.metrics.amm_quote import amm_quote_for_cex
from monitor.metrics.config import CaptureConfig, MetricsConfig
from monitor.metrics.edge import Direction, mid_from_bid_ask
from monitor.metrics.pnl_snapshot import levels_from_depth_curve, rfq_tick_to_poll_quote
from monitor.metrics.pnl_v2 import compute_pnl_usd
from monitor.metrics.session import session_kind
from monitor.metrics.withdrawal import withdrawal_params_from_pair
from monitor.quotes import (
    BybitBookTick,
    BybitDepthTick,
    FluxionPoolStateTick,
    FluxionRfqQuoteTick,
)
from monitor.storage.reader import JournalReader
from monitor.symbols.bstocks_models import BStocksPair
from monitor.symbols.models import Pair

DIRECTIONS: tuple[Direction, Direction] = (
    "buy_fluxion_sell_bybit",
    "buy_bybit_sell_fluxion",
)


def _config_with_live_basis(
    metrics_cfg: MetricsConfig,
    ts_ms: int,
    *,
    basis_ts_ms: Sequence[int] | None,
    basis_bps_series: Sequence[Decimal] | None,
    basis_max_age_ms: int | None,
) -> MetricsConfig | None:
    """Return config with as-of basis, or None if a live series was required but missing.

    When no basis series is provided, returns ``metrics_cfg`` unchanged (panel
    path uses the market constant). When a series *is* provided, a successful
    as-of join is required — never silently fall back to the constant (WHI-909).
    """
    if basis_ts_ms is None or basis_bps_series is None:
        return metrics_cfg
    live = as_of_value(
        basis_ts_ms,
        basis_bps_series,
        ts_ms,
        max_age_ms=basis_max_age_ms,
    )
    if live is None:
        return None
    return metrics_cfg.model_copy(update={"usdt_usdc_basis_bps": live})

CaptureStatus = Literal[
    "ok",
    "disabled",
    "insufficient",
    "no_samples",
    "no_pool",
]

DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class GapInterval:
    """Collector downtime interval (gap samples must not glue windows)."""

    start_ms: int
    end_ms: int
    source: str = "collector_down"


@dataclass(frozen=True, slots=True)
class CaptureSeriesStats:
    """One homogeneous (pair, direction, session, venue, size) series."""

    pair_id: str
    direction: Direction
    session: Literal["open", "closed"]
    venue: Literal["amm", "rfq"]
    size_usd: Decimal
    n_samples: int
    n_windows: int
    windows_per_day: float
    capturable_usd: Decimal
    capturable_usd_per_day: Decimal
    span_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "direction": self.direction,
            "session": self.session,
            "venue": self.venue,
            "size_usd": format(self.size_usd, "f"),
            "n_samples": self.n_samples,
            "n_windows": self.n_windows,
            "windows_per_day": self.windows_per_day,
            "capturable_usd": format(self.capturable_usd, "f"),
            "capturable_usd_per_day": format(self.capturable_usd_per_day, "f"),
            "span_ms": self.span_ms,
        }


@dataclass(frozen=True, slots=True)
class CaptureSparkPoint:
    """One bucket for the detail windows/day sparkline."""

    bucket_start_ms: int
    n_windows: int
    capturable_usd: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket_start_ms": self.bucket_start_ms,
            "n_windows": self.n_windows,
            "capturable_usd": format(self.capturable_usd, "f"),
        }


@dataclass(frozen=True, slots=True)
class CapturePairSnapshot:
    """Per-pair capture card for overview + detail (WHI-963)."""

    status: CaptureStatus
    pair_id: str
    lookback_ms: int
    span_ms: int
    since_ms: int
    until_ms: int
    trade_duration_ms: int
    reentry_cooldown_ms: int
    size_usd: Decimal
    # Best series by capturable_usd_per_day (overview headline).
    windows_per_day: float | None = None
    capturable_usd_per_day: Decimal | None = None
    n_windows: int | None = None
    direction: Direction | None = None
    session: Literal["open", "closed"] | None = None
    venue: Literal["amm", "rfq"] | None = None
    # All non-empty series (detail breakdown).
    series: tuple[CaptureSeriesStats, ...] = ()
    # Windows starting in each sparkline bucket (best direction, all sessions).
    sparkline: tuple[CaptureSparkPoint, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "pair_id": self.pair_id,
            "lookback_ms": self.lookback_ms,
            "span_ms": self.span_ms,
            "since_ms": self.since_ms,
            "until_ms": self.until_ms,
            "trade_duration_ms": self.trade_duration_ms,
            "reentry_cooldown_ms": self.reentry_cooldown_ms,
            "size_usd": format(self.size_usd, "f"),
            "windows_per_day": self.windows_per_day,
            "capturable_usd_per_day": (
                None
                if self.capturable_usd_per_day is None
                else format(self.capturable_usd_per_day, "f")
            ),
            "n_windows": self.n_windows,
            "direction": self.direction,
            "session": self.session,
            "venue": self.venue,
            "series": [s.to_dict() for s in self.series],
            "sparkline": [p.to_dict() for p in self.sparkline],
        }

    def overview_wire(self) -> dict[str, Any]:
        """Compact nested card for overview rows (no series dump)."""
        return {
            "status": self.status,
            "windows_per_day": self.windows_per_day,
            "capturable_usd_per_day": (
                None
                if self.capturable_usd_per_day is None
                else format(self.capturable_usd_per_day, "f")
            ),
            "n_windows": self.n_windows,
            "direction": self.direction,
            "session": self.session,
            "venue": self.venue,
            "span_ms": self.span_ms,
            "lookback_ms": self.lookback_ms,
            "size_usd": format(self.size_usd, "f"),
        }


def in_gap(ts_ms: int, gaps: Sequence[GapInterval]) -> bool:
    """True when ``ts_ms`` falls inside a sorted gap list."""
    for g in gaps:
        if g.start_ms <= ts_ms <= g.end_ms:
            return True
        if g.start_ms > ts_ms:
            break
    return False


def as_of_idx(ts_list: Sequence[int], ts_ms: int) -> int | None:
    """Rightmost index with ``ts_list[i] <= ts_ms``, or None."""
    i = bisect.bisect_right(ts_list, ts_ms) - 1
    return i if i >= 0 else None


def series_stats(
    samples: Sequence[EdgeSample],
    *,
    span_ms: int,
    max_gap_ms: int,
    reentry_cooldown_ms: int,
    trade_duration_ms: int,
    min_edge_bps: Decimal = Decimal(0),
    max_trade_usd: Decimal | None = None,
) -> tuple[CaptureSeriesStats, list[OpportunityWindow]] | None:
    """Window + single-flight stats for one homogeneous series.

    Returns ``(stats, windows)`` so callers can reuse windows for sparklines
    without re-running ``detect_windows``. None when samples is empty.
    """
    if not samples:
        return None
    if span_ms <= 0:
        raise ValueError("span_ms must be positive")
    ordered = sorted(samples, key=lambda s: s.ts_ms)
    wins = detect_windows(
        ordered, min_edge_bps=min_edge_bps, max_gap_ms=max_gap_ms
    )
    profit = capturable_profit_single_flight(
        wins,
        reentry_cooldown_ms=reentry_cooldown_ms,
        trade_duration_ms=trade_duration_ms,
        max_trade_usd=max_trade_usd,
    )
    days = Decimal(span_ms) / Decimal(DAY_MS)
    n = len(wins)
    per_day = float(n) / float(days) if days > 0 else 0.0
    profit_day = profit / days if days > 0 else Decimal(0)
    s0 = ordered[0]
    stats = CaptureSeriesStats(
        pair_id=s0.pair_id,
        direction=s0.direction,  # type: ignore[arg-type]
        session=s0.session,
        venue=s0.venue,
        size_usd=s0.size_usd,
        n_samples=len(ordered),
        n_windows=n,
        windows_per_day=per_day,
        capturable_usd=profit,
        capturable_usd_per_day=profit_day,
        span_ms=span_ms,
    )
    return stats, wins


def sparkline_from_windows(
    windows: Sequence[OpportunityWindow],
    *,
    since_ms: int,
    until_ms: int,
    bucket_ms: int,
) -> list[CaptureSparkPoint]:
    """Bucket windows by start_ms into fixed-width sparkline points."""
    if bucket_ms <= 0:
        raise ValueError("bucket_ms must be positive")
    if until_ms < since_ms:
        return []
    # Align first bucket to since_ms floor.
    start_bucket = (since_ms // bucket_ms) * bucket_ms
    end_bucket = (until_ms // bucket_ms) * bucket_ms
    counts: dict[int, int] = {}
    profits: dict[int, Decimal] = {}
    for w in windows:
        b = (w.start_ms // bucket_ms) * bucket_ms
        if b < start_bucket or b > end_bucket:
            continue
        counts[b] = counts.get(b, 0) + 1
        profits[b] = profits.get(b, Decimal(0)) + max(Decimal(0), w.trade_pnl_usd)
    out: list[CaptureSparkPoint] = []
    b = start_bucket
    while b <= end_bucket:
        out.append(
            CaptureSparkPoint(
                bucket_start_ms=b,
                n_windows=counts.get(b, 0),
                capturable_usd=profits.get(b, Decimal(0)),
            )
        )
        b += bucket_ms
    return out


def build_amm_samples(
    *,
    pair: Pair | BStocksPair,
    books: Sequence[BybitBookTick],
    pools: Sequence[FluxionPoolStateTick],
    depths: Sequence[BybitDepthTick],
    metrics_cfg: MetricsConfig,
    quote_decimals: int,
    size_usd: Decimal,
    gaps: Sequence[GapInterval],
    align_ms: int,
    max_abs_spread_bps: Decimal | None,
    basis_ts_ms: Sequence[int] | None = None,
    basis_bps_series: Sequence[Decimal] | None = None,
    basis_max_age_ms: int | None = None,
) -> list[EdgeSample]:
    """Score AMM samples at book times (same algorithm as xstocks_edge_quant).

    Optional ``basis_ts_ms`` / ``basis_bps_series`` (parallel, ascending) override
    ``metrics_cfg.usdt_usdc_basis_bps`` per timestamp via as-of join (WHI-909
    live USDCUSDT premium). When omitted, the config constant is used. When
    provided, samples without a join within ``basis_max_age_ms`` are skipped
    (no silent fallback to the constant).
    """
    if not books or not pools:
        return []
    wd = withdrawal_params_from_pair(pair)
    if (basis_ts_ms is None) ^ (basis_bps_series is None):
        raise ValueError(
            "basis_ts_ms and basis_bps_series must both be set or both omitted"
        )
    if basis_ts_ms is not None and len(basis_ts_ms) != len(basis_bps_series or ()):
        raise ValueError("basis_ts_ms and basis_bps_series length mismatch")
    pool_ts = [p.recv_ts_ms for p in pools]
    depth_ts = [d.recv_ts_ms for d in depths]
    out: list[EdgeSample] = []
    for book in books:
        ts = book.recv_ts_ms
        if in_gap(ts, gaps):
            continue
        pi = as_of_idx(pool_ts, ts)
        if pi is None:
            continue
        pool = pools[pi]
        if ts - pool.recv_ts_ms > align_ms:
            continue
        amm = amm_pool_from_pair_tick(pair, pool, quote_decimals=quote_decimals)
        if amm is None:
            continue
        cex_mid = mid_from_bid_ask(book.bid_de_multiplied, book.ask_de_multiplied)
        if cex_mid is None or cex_mid <= 0:
            continue
        _, reason = amm_quote_for_cex(
            pool,
            cex_mid=cex_mid,
            max_abs_spread_bps=max_abs_spread_bps,
        )
        if reason is not None:
            continue
        bids = asks = None
        di = as_of_idx(depth_ts, ts) if depth_ts else None
        if di is not None and ts - depths[di].recv_ts_ms <= align_ms:
            bids = levels_from_depth_curve(depths[di], side="bid") or None
            asks = levels_from_depth_curve(depths[di], side="ask") or None
        sess = session_kind(
            datetime.fromtimestamp(ts / 1000, tz=UTC), config=metrics_cfg
        ).value
        cfg = _config_with_live_basis(
            metrics_cfg,
            ts,
            basis_ts_ms=basis_ts_ms,
            basis_bps_series=basis_bps_series,
            basis_max_age_ms=basis_max_age_ms,
        )
        if cfg is None:
            continue
        for direction in DIRECTIONS:
            # WHI-1090: unknown-fee dir2 is unpriced — do not let it
            # inflate Cap $/d or headline the capture card.
            if (
                direction == "buy_bybit_sell_fluxion"
                and wd.asset_fee_tokens is None
            ):
                continue
            r = compute_pnl_usd(
                pair_id=pair.id,
                bybit_bid=book.bid_de_multiplied,
                bybit_ask=book.ask_de_multiplied,
                size_usd=size_usd,
                direction=direction,
                venue="amm",
                config=cfg,
                amm=amm,
                bybit_bids=bids,
                bybit_asks=asks,
                asset_withdrawal_fee_tokens=wd.asset_fee_tokens,
                price_multiplier=wd.price_multiplier,
            )
            if not r.fillable or r.pnl_bps is None:
                continue
            out.append(
                EdgeSample(
                    ts_ms=ts,
                    pair_id=pair.id,
                    direction=direction,
                    session=sess,  # type: ignore[arg-type]
                    venue="amm",
                    size_usd=size_usd,
                    edge_bps=r.pnl_bps,
                    pnl_usd=r.pnl_usd,
                )
            )
    return out


def build_rfq_samples(
    *,
    pair: Pair | BStocksPair,
    books: Sequence[BybitBookTick],
    rfq_ticks: Sequence[FluxionRfqQuoteTick],
    metrics_cfg: MetricsConfig,
    gaps: Sequence[GapInterval],
    align_ms: int,
    max_trade_usd: Decimal,
    basis_ts_ms: Sequence[int] | None = None,
    basis_bps_series: Sequence[Decimal] | None = None,
    basis_max_age_ms: int | None = None,
) -> list[EdgeSample]:
    """Score RFQ samples at poll times (poll-native size, capped for single-flight)."""
    if not books or not rfq_ticks:
        return []
    wd = withdrawal_params_from_pair(pair)
    if (basis_ts_ms is None) ^ (basis_bps_series is None):
        raise ValueError(
            "basis_ts_ms and basis_bps_series must both be set or both omitted"
        )
    if basis_ts_ms is not None and len(basis_ts_ms) != len(basis_bps_series or ()):
        raise ValueError("basis_ts_ms and basis_bps_series length mismatch")
    book_ts = [b.recv_ts_ms for b in books]
    out: list[EdgeSample] = []
    if isinstance(pair, BStocksPair):
        native_dec = pair.pancake.native_decimals
    else:
        native_dec = pair.fluxion.native_decimals
    for tick in rfq_ticks:
        ts = tick.poll_ts_ms
        if in_gap(ts, gaps):
            continue
        poll = rfq_tick_to_poll_quote(tick, native_decimals=native_dec)
        if poll is None:
            continue
        bi = as_of_idx(book_ts, ts)
        if bi is None:
            continue
        book = books[bi]
        if ts - book.recv_ts_ms > align_ms:
            continue
        if poll.fluxion_leg == "buy":
            direction: Direction = "buy_fluxion_sell_bybit"
        else:
            direction = "buy_bybit_sell_fluxion"
        cfg = _config_with_live_basis(
            metrics_cfg,
            ts,
            basis_ts_ms=basis_ts_ms,
            basis_bps_series=basis_bps_series,
            basis_max_age_ms=basis_max_age_ms,
        )
        if cfg is None:
            continue
        if direction == "buy_bybit_sell_fluxion" and wd.asset_fee_tokens is None:
            continue
        r = compute_pnl_usd(
            pair_id=pair.id,
            bybit_bid=book.bid_de_multiplied,
            bybit_ask=book.ask_de_multiplied,
            size_usd=Decimal(1),  # ignored for RFQ path
            direction=direction,
            venue="rfq",
            config=cfg,
            rfq=poll,
            asset_withdrawal_fee_tokens=wd.asset_fee_tokens,
            price_multiplier=wd.price_multiplier,
        )
        if not r.fillable or r.pnl_bps is None or r.size_usd <= 0:
            continue
        if r.size_usd > max_trade_usd > 0:
            scale = max_trade_usd / r.size_usd
            pnl = r.pnl_usd * scale
            size = max_trade_usd
            bps = pnl / size * Decimal(10_000)
        else:
            pnl = r.pnl_usd
            size = r.size_usd
            bps = r.pnl_bps
        sess = session_kind(
            datetime.fromtimestamp(ts / 1000, tz=UTC), config=metrics_cfg
        ).value
        out.append(
            EdgeSample(
                ts_ms=ts,
                pair_id=pair.id,
                direction=direction,
                session=sess,  # type: ignore[arg-type]
                venue="rfq",
                size_usd=size,
                edge_bps=bps,
                pnl_usd=pnl,
            )
        )
    return out


def _group_key(
    s: EdgeSample,
) -> tuple[str, str, str, str]:
    return (s.pair_id, s.direction, s.session, s.venue)


def _placeholder(
    status: CaptureStatus,
    *,
    pair_id: str,
    capture: CaptureConfig,
    since_ms: int,
    until_ms: int,
    span_ms: int | None = None,
) -> CapturePairSnapshot:
    """Shared empty / disabled / insufficient card (same base fields)."""
    return CapturePairSnapshot(
        status=status,
        pair_id=pair_id,
        lookback_ms=capture.lookback_ms,
        span_ms=span_ms if span_ms is not None else max(1, until_ms - since_ms),
        since_ms=since_ms,
        until_ms=until_ms,
        trade_duration_ms=capture.trade_duration_ms,
        reentry_cooldown_ms=capture.reentry_cooldown_ms,
        size_usd=capture.size_usd,
    )


def compute_capture_from_samples(
    samples: Sequence[EdgeSample],
    *,
    pair_id: str,
    since_ms: int,
    until_ms: int,
    capture: CaptureConfig,
    lookback_since_ms: int | None = None,
) -> CapturePairSnapshot:
    """Aggregate EdgeSamples into a pair-level capture snapshot.

    Picks the best (direction, venue) by capturable $/day for the overview
    headline (open+closed summed). Sparkline uses that direction's windows
    across sessions (same venue).

    ``span_ms`` for rates is **actual sample coverage**
    ``[min(ts), max(ts)]`` when samples exist — not the configured lookback —
    so a 3 h journal under a 24 h lookback does not understate Cap $/d by 8×.
    ``lookback_since_ms`` is recorded on the wire as the configured floor.
    """
    cfg_since = lookback_since_ms if lookback_since_ms is not None else since_ms
    if not samples:
        return _placeholder(
            "no_samples",
            pair_id=pair_id,
            capture=capture,
            since_ms=cfg_since,
            until_ms=until_ms,
        )

    # Coverage span from observed samples (matches edge_quant book_span style).
    ts_min = min(s.ts_ms for s in samples)
    ts_max = max(s.ts_ms for s in samples)
    span_ms = max(1, ts_max - ts_min)
    # Wire since/until clamped to observed data so hover shows what was used.
    effective_since = max(cfg_since, ts_min)
    effective_until = max(effective_since, min(until_ms, ts_max))

    groups: dict[tuple[str, str, str, str], list[EdgeSample]] = {}
    for s in samples:
        groups.setdefault(_group_key(s), []).append(s)

    series_list: list[CaptureSeriesStats] = []
    windows_by_key: dict[tuple[str, str, str, str], list[OpportunityWindow]] = {}
    for key, group in groups.items():
        result = series_stats(
            group,
            span_ms=span_ms,
            max_gap_ms=capture.max_gap_ms,
            reentry_cooldown_ms=capture.reentry_cooldown_ms,
            trade_duration_ms=capture.trade_duration_ms,
            min_edge_bps=capture.min_edge_bps,
            max_trade_usd=capture.size_usd,
        )
        if result is None:
            continue
        st, wins = result
        series_list.append(st)
        windows_by_key[key] = wins

    if not series_list:
        return _placeholder(
            "no_samples",
            pair_id=pair_id,
            capture=capture,
            since_ms=cfg_since,
            until_ms=until_ms,
            span_ms=span_ms,
        )

    # Best series by capturable $/day, then windows/day as tiebreak.
    best = max(
        series_list,
        key=lambda s: (s.capturable_usd_per_day, s.windows_per_day, s.n_windows),
    )

    # Sparkline: windows for best direction+venue across both sessions.
    spark_wins: list[OpportunityWindow] = []
    for key, wins in windows_by_key.items():
        _pid, direction, _sess, venue = key
        if direction == best.direction and venue == best.venue:
            spark_wins.extend(wins)
    spark = sparkline_from_windows(
        spark_wins,
        since_ms=effective_since,
        until_ms=max(effective_until, effective_since),
        bucket_ms=capture.sparkline_bucket_ms,
    )

    # Overview headline: sum open+closed for the winning direction+venue so
    # a full day of capture is not undercounted by session split.
    headline_windows = 0
    headline_profit = Decimal(0)
    sessions_in_headline: set[str] = set()
    for st in series_list:
        if st.direction == best.direction and st.venue == best.venue:
            headline_windows += st.n_windows
            headline_profit += st.capturable_usd
            sessions_in_headline.add(st.session)
    days = Decimal(span_ms) / Decimal(DAY_MS)
    headline_wpd = float(headline_windows) / float(days) if days > 0 else 0.0
    headline_ppd = headline_profit / days if days > 0 else Decimal(0)
    # session is only set when the headline is a single session; when open+closed
    # are summed, leave null so the wire does not imply "all N windows were open".
    headline_session: Literal["open", "closed"] | None = None
    if len(sessions_in_headline) == 1:
        only = next(iter(sessions_in_headline))
        if only in ("open", "closed"):
            headline_session = only  # type: ignore[assignment]

    return CapturePairSnapshot(
        status="ok",
        pair_id=pair_id,
        lookback_ms=capture.lookback_ms,
        span_ms=span_ms,
        since_ms=effective_since,
        until_ms=max(effective_until, effective_since),
        trade_duration_ms=capture.trade_duration_ms,
        reentry_cooldown_ms=capture.reentry_cooldown_ms,
        size_usd=capture.size_usd,
        windows_per_day=headline_wpd,
        capturable_usd_per_day=headline_ppd,
        n_windows=headline_windows,
        direction=best.direction,
        session=headline_session,
        venue=best.venue,
        series=tuple(
            sorted(
                series_list,
                key=lambda s: (
                    -s.capturable_usd_per_day,
                    s.venue,
                    s.direction,
                    s.session,
                ),
            )
        ),
        sparkline=tuple(spark),
    )


def disabled_snapshot(
    pair_id: str,
    *,
    capture: CaptureConfig,
    now_ms: int,
) -> CapturePairSnapshot:
    """Placeholder when capture is turned off in config."""
    until = now_ms
    since = max(0, until - capture.lookback_ms)
    return _placeholder(
        "disabled",
        pair_id=pair_id,
        capture=capture,
        since_ms=since,
        until_ms=until,
    )


def build_capture_pair_snapshot(
    *,
    pair: Pair | BStocksPair,
    reader: JournalReader,
    metrics: MetricsConfig,
    quote_decimals: int,
    now_ms: int,
    has_rfq: bool = False,
) -> CapturePairSnapshot:
    """Load journal ticks for ``pair`` and compute capture stats (I/O seam).

    Parity with ``scripts/xstocks_edge_quant.py`` on the same span:
    pass the same ``sample_ms`` / ``align_ms`` / ``max_gap_ms`` /
    ``trade_duration_ms`` / ``reentry_cooldown_ms`` / ``size_usd`` and
    compare ``series_stats`` to the script's T=0 ``threshold_sweep`` row
    (``capturable_profit_per_day`` / ``windows_per_day``).
    """
    capture = metrics.capture
    if not capture.enabled:
        return disabled_snapshot(pair.id, capture=capture, now_ms=now_ms)

    until_ms = now_ms
    since_ms = max(0, until_ms - capture.lookback_ms)

    has_amm = False
    if isinstance(pair, Pair):
        has_amm = pair.fluxion.amm is not None
    elif isinstance(pair, BStocksPair):
        has_amm = pair.pancake.amm is not None
    want_rfq = capture.include_rfq and has_rfq
    # dex:none with RFQ off → no capture path at all.
    if not has_amm and not want_rfq:
        return _placeholder(
            "no_pool",
            pair_id=pair.id,
            capture=capture,
            since_ms=since_ms,
            until_ms=until_ms,
        )

    # Align pool/depth slightly earlier so the first book sample can join.
    load_since = max(0, since_ms - capture.align_ms)

    gaps_raw = reader.collector_down_gaps(since_ms=load_since, until_ms=until_ms)
    gaps = [
        GapInterval(start_ms=g.gap_start_ms, end_ms=g.gap_end_ms, source=g.source)
        for g in gaps_raw
    ]
    books = reader.bucketed_bybit_books(
        pair.id,
        sample_ms=capture.sample_ms,
        since_ms=since_ms,
        until_ms=until_ms,
    )
    if not books:
        return _placeholder(
            "insufficient",
            pair_id=pair.id,
            capture=capture,
            since_ms=since_ms,
            until_ms=until_ms,
        )

    samples: list[EdgeSample] = []
    if has_amm:
        # Bucket pools to sample_ms (not raw ~2s Mantle ticks) — only as-of
        # at book times is needed; keeps cold overview under the P95 budget.
        pools = reader.pool_states_range(
            pair.id,
            since_ms=load_since,
            until_ms=until_ms,
            sample_ms=capture.sample_ms,
        )
        depths: list[BybitDepthTick] = []
        if capture.use_depth:
            depths = reader.bybit_depths_range(
                pair.id,
                since_ms=load_since,
                until_ms=until_ms,
                sample_ms=capture.sample_ms,
            )
        if pools:
            samples.extend(
                build_amm_samples(
                    pair=pair,
                    books=books,
                    pools=pools,
                    depths=depths,
                    metrics_cfg=metrics,
                    quote_decimals=quote_decimals,
                    size_usd=capture.size_usd,
                    gaps=gaps,
                    align_ms=capture.align_ms,
                    max_abs_spread_bps=metrics.max_abs_amm_spread_bps,
                )
            )
    if want_rfq:
        rfq_ticks = reader.rfq_quotes_range(
            pair.id, since_ms=since_ms, until_ms=until_ms
        )
        samples.extend(
            build_rfq_samples(
                pair=pair,
                books=books,
                rfq_ticks=rfq_ticks,
                metrics_cfg=metrics,
                gaps=gaps,
                align_ms=capture.align_ms,
                max_trade_usd=capture.size_usd,
            )
        )
    if not samples:
        return _placeholder(
            "insufficient" if has_amm else "no_samples",
            pair_id=pair.id,
            capture=capture,
            since_ms=since_ms,
            until_ms=until_ms,
        )
    return compute_capture_from_samples(
        samples,
        pair_id=pair.id,
        since_ms=since_ms,
        until_ms=until_ms,
        capture=capture,
        lookback_since_ms=since_ms,
    )
