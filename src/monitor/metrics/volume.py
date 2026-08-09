"""CEX/DEX 24h volume compare helpers (WHI-777).

* **CEX** volume comes from REST-polled ticks (authoritative rolling 24h).
* **DEX** volume is aggregated from collected AMM swaps (truncated when the
  collector has less than a full window of history).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from monitor.metrics.config import MetricsConfig
from monitor.metrics.session import SessionKind, session_kind
from monitor.quotes import BybitTradeTick, CexVolumeTick, FluxionSwapTick


def swap_notional_usd(swap: FluxionSwapTick, *, quote_is_token0: bool) -> Decimal:
    """Absolute quote-leg notional (USDC/USDT human units).

    Lives in metrics (M3) so attribution can import it without an upward
    dependency (DESIGN §4.2).
    """
    leg = swap.amount_token0 if quote_is_token0 else swap.amount_token1
    return abs(leg)


@dataclass(frozen=True, slots=True)
class SessionVolumeSlice:
    """Notional + print count for one NYSE session bucket."""

    volume_usd: Decimal
    trade_count: int


@dataclass(frozen=True, slots=True)
class DexVolumeWindow:
    """DEX (AMM swap) volume over a rolling window, with truncation metadata."""

    volume_usd: Decimal
    trade_count: int
    open: SessionVolumeSlice
    closed: SessionVolumeSlice
    # Inclusive data start actually used (max of requested since and earliest print).
    window_start_ms: int
    requested_since_ms: int
    now_ms: int
    truncated: bool
    # Earliest swap recv_ts in journal for this pair (None if none ever).
    earliest_recv_ts_ms: int | None


@dataclass(frozen=True, slots=True)
class CexJournalVolumeWindow:
    """Optional CEX session split from self-collected trades (partial).

    Headline CEX 24h always comes from REST; this is only for open/closed
    segmentation on the detail panel when journal coverage exists.
    """

    volume_usd: Decimal
    trade_count: int
    open: SessionVolumeSlice
    closed: SessionVolumeSlice
    window_start_ms: int
    truncated: bool


# Why overview CEX Vol / ratio is blank (WHI-974). Keep vocabulary stable —
# same family as no_pool / empty_pool / pricing_anomaly.
CexVolumeReason = Literal["geo_blocked"]


@dataclass(frozen=True, slots=True)
class VolumeCompare:
    """Pair-level CEX vs DEX volume snapshot for overview + detail.

    Overview **CEX Vol** is exchange-reported REST turnover only. When REST is
    geo-blocked the figure stays null with ``cex_volume_reason='geo_blocked'``;
    journal-derived notional never fills the overview column or ``volume_ratio``
    (owner lock WHI-974). Journal session split remains on detail only via
    ``cex_journal``.
    """

    cex_volume_24h: Decimal | None
    cex_trade_count_24h: int | None
    cex_source: str | None
    cex_poll_ts_ms: int | None
    dex: DexVolumeWindow
    # Journal-derived CEX open/closed (None when no trades in window).
    cex_journal: CexJournalVolumeWindow | None
    volume_ratio: Decimal | None  # cex / dex when both > 0 and cex known
    # WHI-974: explicit blank reason (geo_blocked) when REST is unavailable.
    cex_volume_reason: CexVolumeReason | None = None


def volume_ratio(cex: Decimal | None, dex: Decimal) -> Decimal | None:
    """CEX/DEX notional ratio. None when CEX missing or DEX not positive."""
    if cex is None or dex <= 0:
        return None
    return cex / dex


def aggregate_dex_volume(
    swaps: list[FluxionSwapTick],
    *,
    quote_is_token0: bool,
    since_ms: int,
    now_ms: int,
    metrics: MetricsConfig,
    earliest_recv_ts_ms: int | None = None,
    collector_started_ms: int | None = None,
) -> DexVolumeWindow:
    """Sum USDC-leg notional of swaps in ``[since_ms, now_ms]``.

    Truncation is keyed off **collector coverage** (``collector_started_ms``),
    not the first swap on this pair — an illiquid pair with zero swaps on a
    young collector must still show "since HH:MM" rather than a full 24h zero.
    """
    # Coverage floor: min(first-start meta, earliest retained swap). Using min
    # fixes the upgrade path where first-start is stamped "now" on a journal
    # that already holds months of permanent swaps.
    earliest = earliest_recv_ts_ms
    if earliest is None and swaps:
        earliest = min(s.recv_ts_ms for s in swaps)

    coverage_candidates = [
        t for t in (collector_started_ms, earliest) if t is not None
    ]
    coverage_start = min(coverage_candidates) if coverage_candidates else None

    effective_start = since_ms
    truncated = False
    if coverage_start is not None and coverage_start > since_ms:
        effective_start = coverage_start
        truncated = True

    total = Decimal(0)
    count = 0
    open_vol = Decimal(0)
    open_n = 0
    closed_vol = Decimal(0)
    closed_n = 0

    for s in swaps:
        if s.recv_ts_ms < since_ms or s.recv_ts_ms > now_ms:
            continue
        if s.direction not in ("buy_native", "sell_native"):
            continue
        notional = swap_notional_usd(s, quote_is_token0=quote_is_token0)
        total += notional
        count += 1
        try:
            sess = session_kind(
                datetime.fromtimestamp(s.block_ts, tz=UTC),
                config=metrics,
            )
        except ValueError:
            sess = SessionKind.CLOSED
        if sess is SessionKind.OPEN:
            open_vol += notional
            open_n += 1
        else:
            closed_vol += notional
            closed_n += 1

    return DexVolumeWindow(
        volume_usd=total,
        trade_count=count,
        open=SessionVolumeSlice(volume_usd=open_vol, trade_count=open_n),
        closed=SessionVolumeSlice(volume_usd=closed_vol, trade_count=closed_n),
        window_start_ms=effective_start,
        requested_since_ms=since_ms,
        now_ms=now_ms,
        truncated=truncated,
        earliest_recv_ts_ms=earliest,
    )


def aggregate_cex_journal_volume(
    trades: list[BybitTradeTick],
    *,
    since_ms: int,
    now_ms: int,
    metrics: MetricsConfig,
) -> CexJournalVolumeWindow | None:
    """Session-split CEX notional from journal trades (secondary / partial)."""
    if not trades:
        return None
    earliest = min(t.exchange_ts_ms for t in trades)
    truncated = earliest > since_ms
    effective_start = max(since_ms, earliest)

    total = Decimal(0)
    count = 0
    open_vol = Decimal(0)
    open_n = 0
    closed_vol = Decimal(0)
    closed_n = 0
    for t in trades:
        if t.exchange_ts_ms < since_ms or t.exchange_ts_ms > now_ms:
            continue
        # Venue-listed price × size = quote notional (matches REST turnover).
        # Do not use price_de_multiplied (comparable units only).
        notional = t.price * t.size
        total += notional
        count += 1
        try:
            sess = session_kind(
                datetime.fromtimestamp(t.exchange_ts_ms / 1000, tz=UTC),
                config=metrics,
            )
        except ValueError:
            sess = SessionKind.CLOSED
        if sess is SessionKind.OPEN:
            open_vol += notional
            open_n += 1
        else:
            closed_vol += notional
            closed_n += 1

    if count == 0:
        return None
    return CexJournalVolumeWindow(
        volume_usd=total,
        trade_count=count,
        open=SessionVolumeSlice(volume_usd=open_vol, trade_count=open_n),
        closed=SessionVolumeSlice(volume_usd=closed_vol, trade_count=closed_n),
        window_start_ms=effective_start,
        truncated=truncated,
    )


def build_volume_compare(
    *,
    cex_tick: CexVolumeTick | None,
    swaps: list[FluxionSwapTick],
    quote_is_token0: bool,
    since_ms: int,
    now_ms: int,
    metrics: MetricsConfig,
    earliest_swap_recv_ts_ms: int | None = None,
    collector_started_ms: int | None = None,
    journal_trades: list[BybitTradeTick] | None = None,
    cex_volume_reason: CexVolumeReason | None = None,
) -> VolumeCompare:
    """Assemble overview/detail volume compare for one pair.

    Journal trades feed ``cex_journal`` (detail panel) only. They never fill
    ``cex_volume_24h``, ``cex_trade_count_24h``, or ``volume_ratio`` — those
    stay exchange-REST-only so a geo-blocked host cannot silently look like a
    partial REST print (WHI-974).
    """
    dex = aggregate_dex_volume(
        swaps,
        quote_is_token0=quote_is_token0,
        since_ms=since_ms,
        now_ms=now_ms,
        metrics=metrics,
        earliest_recv_ts_ms=earliest_swap_recv_ts_ms,
        collector_started_ms=collector_started_ms,
    )
    cex_vol: Decimal | None = None
    cex_n: int | None = None
    cex_src: str | None = None
    cex_poll: int | None = None
    if cex_tick is not None:
        cex_vol = cex_tick.volume_quote_24h
        cex_n = cex_tick.trade_count_24h
        cex_src = cex_tick.source
        cex_poll = cex_tick.poll_ts_ms
        # REST present → clear blank reason even if meta is stale mid-poll.
        reason: CexVolumeReason | None = None
    else:
        reason = cex_volume_reason

    journal = None
    if journal_trades is not None:
        journal = aggregate_cex_journal_volume(
            journal_trades,
            since_ms=since_ms,
            now_ms=now_ms,
            metrics=metrics,
        )
    # Do NOT back-fill cex_trade_count_24h from journal when REST is missing —
    # that mixed a journal count next to a blank REST volume (WHI-974).
    # Journal print count lives only under cex_journal.trade_count.

    return VolumeCompare(
        cex_volume_24h=cex_vol,
        cex_trade_count_24h=cex_n,
        cex_source=cex_src,
        cex_poll_ts_ms=cex_poll,
        dex=dex,
        cex_journal=journal,
        volume_ratio=volume_ratio(cex_vol, dex.volume_usd),
        cex_volume_reason=reason,
    )


