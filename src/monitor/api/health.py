"""Collector health snapshot for GET /api/health (pure over JournalReader)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Literal

from monitor.cex_volume.poller import (
    META_CEX_VOLUME_FIRST_MS,
    META_CEX_VOLUME_HOST,
    META_CEX_VOLUME_HTTP_STATUS,
    META_CEX_VOLUME_LAST_MS,
    META_CEX_VOLUME_STATUS,
    META_CEX_VOLUME_VENUE,
)
from monitor.collector.gaps import SOURCE_COLLECTOR_DOWN
from monitor.collector.watchdog import META_HEARTBEAT, META_LAST_TICK_WRITE
from monitor.markets.ids import DEFAULT_MARKET_ID
from monitor.quotes import CollectorGap, now_ms
from monitor.storage import JournalReader
from monitor.storage.reader import RfqQuoteCoverage, rfq_quote_coverage
from monitor.underlying.coverage_probe import (
    META_MISMATCHES,
    META_PROBE_ERRORS,
    META_PROBE_MS,
    META_UNPUBLISHED,
    errors_from_meta_json,
    mismatches_from_meta_json,
    unpublished_from_meta_json,
)

# WHI-825 three-state feed vocabulary (banner + status bar).
FeedState = Literal["ok", "feed_down", "feed_quiet", "gap"]

# Default: data older than this while the process is still heartbeating → quiet.
DEFAULT_DATA_QUIET_MS = 60_000

# Rolling window for RFQ error-rate on /api/health (WHI-974).
DEFAULT_RFQ_ERROR_WINDOW_MS = 15 * 60_000


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Operator-facing collector / journal status."""

    ok: bool
    generated_ts_ms: int
    db_path: str
    db_exists: bool
    collector_started_ms: int | None
    collector_stopped_ms: int | None
    last_block: int | None
    last_block_ingest_latency_ms: float | None
    freshest_recv_ts_ms: int | None
    age_ms: int | None
    collector_alive: bool
    gap_recent: bool
    recent_gaps: list[CollectorGap]
    poll_interval_s: float | None = None
    error: str | None = None
    # WHI-787: config marked uncovered but Yahoo/Pyth now has a public print.
    uncovered_coverage_mismatches: list[dict[str, Any]] = field(default_factory=list)
    uncovered_coverage_probe_ms: int | None = None
    # Source-level probe failures (so total outage ≠ "all clear").
    uncovered_coverage_probe_errors: list[dict[str, Any]] = field(default_factory=list)
    # WHI-794: Hermes feed_id pinned but latest never published (price/time 0).
    unpublished_pyth_feeds: list[dict[str, Any]] = field(default_factory=list)
    # WHI-825: ok | feed_down | feed_quiet | gap
    feed_state: FeedState = "feed_down"
    # Actionable operator hint (which market, how long, how to recover).
    recovery_hint: str | None = None
    # Heartbeat age (process liveness); may differ from data age when quiet.
    heartbeat_age_ms: int | None = None
    # True when a recent gap has source=collector_down.
    collector_down_gap_recent: bool = False
    # WHI-974: RFQ poll outcome counts (error rate visible without journald).
    rfq_error_rate: float | None = None
    rfq_error_rows: int | None = None
    rfq_total_rows: int | None = None
    rfq_availability_among_reachable: float | None = None
    rfq_http_status_counts: dict[int, int] = field(default_factory=dict)
    rfq_coverage_window_ms: int | None = None
    # WHI-974: CEX REST volume geo-block (sourced from journal meta).
    cex_volume_status: str | None = None
    cex_volume_venue: str | None = None
    cex_volume_host: str | None = None
    cex_volume_http_status: int | None = None
    cex_volume_blocked_first_ms: int | None = None
    cex_volume_blocked_last_ms: int | None = None

    @classmethod
    def unavailable(
        cls,
        *,
        db_path: str,
        now: int | None = None,
        poll_interval_s: float | None = None,
        error: str,
        market_id: str | None = None,
    ) -> HealthStatus:
        """Health when the journal file is missing or unreadable."""
        ts = now if now is not None else now_ms()
        hint = _recovery_hint_missing_db(db_path=db_path, market_id=market_id)
        return cls(
            ok=False,
            generated_ts_ms=ts,
            db_path=db_path,
            db_exists=False,
            collector_started_ms=None,
            collector_stopped_ms=None,
            last_block=None,
            last_block_ingest_latency_ms=None,
            freshest_recv_ts_ms=None,
            age_ms=None,
            collector_alive=False,
            gap_recent=False,
            recent_gaps=[],
            poll_interval_s=poll_interval_s,
            error=error,
            uncovered_coverage_mismatches=[],
            uncovered_coverage_probe_ms=None,
            uncovered_coverage_probe_errors=[],
            unpublished_pyth_feeds=[],
            feed_state="feed_down",
            recovery_hint=hint,
            heartbeat_age_ms=None,
            collector_down_gap_recent=False,
            rfq_error_rate=None,
            rfq_error_rows=None,
            rfq_total_rows=None,
            rfq_availability_among_reachable=None,
            rfq_http_status_counts={},
            rfq_coverage_window_ms=None,
            cex_volume_status=None,
            cex_volume_venue=None,
            cex_volume_host=None,
            cex_volume_http_status=None,
            cex_volume_blocked_first_ms=None,
            cex_volume_blocked_last_ms=None,
        )


def _meta_int(reader: JournalReader, key: str) -> int | None:
    raw = reader.get_meta(key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _meta_float(reader: JournalReader, key: str) -> float | None:
    raw = reader.get_meta(key)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def classify_feed_state(
    *,
    collector_alive: bool,
    data_age_ms: int | None,
    quiet_ms: int,
    collector_down_gap_recent: bool,
) -> FeedState:
    """Three-state (+ ok) feed vocabulary for the panel (WHI-825).

    Priority: feed_down > gap > feed_quiet > ok.

    Only ``collector_down`` gaps elevate ``feed_state`` to ``gap``. Other gap
    sources (WS reconnect, block lag) stay on ``ok``/``feed_quiet`` and are
    still listed in ``recent_gaps`` / ``gap_recent``.
    """
    if not collector_alive:
        return "feed_down"
    if collector_down_gap_recent:
        return "gap"
    if data_age_ms is not None and data_age_ms > quiet_ms:
        return "feed_quiet"
    return "ok"


def _resolve_platform(platform: str | None) -> str:
    return platform if platform is not None else sys.platform


def _ops_restart_hints(*, market_id: str, platform: str | None = None) -> str:
    """Platform-aware check/restart one-liner (WHI-835: no systemctl on macOS)."""
    plat = _resolve_platform(platform)
    mid = market_id
    if plat == "darwin":
        return (
            "Check: ./scripts/dev-web.sh status. "
            "Restart: ./scripts/dev-web.sh restart"
        )
    return (
        f"Check: ./scripts/dev-web.sh status  |  "
        f"sudo systemctl status xstocks-collector@{mid}. "
        f"Restart: ./scripts/dev-web.sh restart  |  "
        f"sudo systemctl restart xstocks-collector@{mid}"
    )


def _recovery_hint_missing_db(
    *,
    db_path: str,
    market_id: str | None,
    platform: str | None = None,
) -> str:
    mid = market_id or DEFAULT_MARKET_ID
    plat = _resolve_platform(platform)
    base = (
        f"journal missing at {db_path}. "
        f"Start collector: ./scripts/dev-web.sh start "
        f"(or python -m monitor.collector --market {mid})"
    )
    if plat == "darwin":
        return base
    return f"{base}; VPS: sudo systemctl start xstocks-collector@{mid}"


def recovery_hint_for_state(
    *,
    feed_state: FeedState,
    market_id: str | None,
    age_ms: int | None,
    collector_down_gap_recent: bool,
    recent_gaps: list[CollectorGap],
    platform: str | None = None,
) -> str | None:
    """Actionable one-liner for operators (local + VPS).

    ``platform`` defaults to ``sys.platform``; pass explicitly in tests.
    Darwin (macOS) omits systemd hints — there is no unit on the laptop
    (WHI-835).
    """
    mid = market_id or DEFAULT_MARKET_ID
    age = ""
    if age_ms is not None:
        if age_ms >= 3_600_000:
            age = f" age={age_ms / 3_600_000:.1f}h"
        elif age_ms >= 60_000:
            age = f" age={age_ms / 60_000:.1f}min"
        else:
            age = f" age={age_ms / 1000:.1f}s"

    if feed_state == "feed_down":
        return (
            f"market={mid} feed down{age}. "
            f"{_ops_restart_hints(market_id=mid, platform=platform)}"
        )
    if feed_state == "gap" or collector_down_gap_recent:
        down = next(
            (g for g in recent_gaps if g.source == SOURCE_COLLECTOR_DOWN),
            None,
        )
        span = ""
        if down is not None:
            span_ms = max(0, down.gap_end_ms - down.gap_start_ms)
            span = f" hole≈{span_ms / 60_000:.1f}min"
        return (
            f"market={mid} known collector downtime recorded{span}. "
            "Cumulative stats exclude this interval; data in the hole is lost."
        )
    if feed_state == "feed_quiet":
        return (
            f"market={mid} collector alive but tick feeds quiet{age}. "
            "Closed-session silence can be normal (WHI-821); "
            "watchdog exits only after process-level write silence."
        )
    return None


def _cex_volume_block_from_meta(
    reader: JournalReader,
) -> dict[str, Any]:
    """Read WHI-974 CEX volume REST block state from journal meta."""
    status = reader.get_meta(META_CEX_VOLUME_STATUS)
    venue = reader.get_meta(META_CEX_VOLUME_VENUE)
    host = reader.get_meta(META_CEX_VOLUME_HOST)
    http_raw = reader.get_meta(META_CEX_VOLUME_HTTP_STATUS)
    first_raw = reader.get_meta(META_CEX_VOLUME_FIRST_MS)
    last_raw = reader.get_meta(META_CEX_VOLUME_LAST_MS)
    http_status: int | None
    try:
        http_status = int(http_raw) if http_raw not in (None, "") else None
    except ValueError:
        http_status = None
    first_ms: int | None
    try:
        first_ms = int(first_raw) if first_raw not in (None, "") else None
    except ValueError:
        first_ms = None
    last_ms: int | None
    try:
        last_ms = int(last_raw) if last_raw not in (None, "") else None
    except ValueError:
        last_ms = None
    return {
        "cex_volume_status": status,
        "cex_volume_venue": venue,
        "cex_volume_host": host,
        "cex_volume_http_status": http_status,
        "cex_volume_blocked_first_ms": first_ms,
        "cex_volume_blocked_last_ms": last_ms,
    }


def build_health(
    reader: JournalReader,
    *,
    now: int | None = None,
    stale_ms: int,
    gap_window_ms: int,
    poll_interval_s: float | None = None,
    quiet_ms: int = DEFAULT_DATA_QUIET_MS,
    market_id: str | None = None,
    rfq_error_window_ms: int = DEFAULT_RFQ_ERROR_WINDOW_MS,
) -> HealthStatus:
    """Derive health from meta keys + (fallback) freshest journal recv.

    ``collector_alive`` prefers ``collector_heartbeat_ms`` (process liveness
    independent of quiet bookTicker). Data age prefers the collector-stamped
    ``collector_last_tick_write_ms`` meta (O(1)) and only falls back to
    ``freshest_recv_ts_ms()`` on pre-WHI-825 journals — that scan is multi-
    hundred-ms on large binance books and blocked market switches.
    """
    ts = now if now is not None else now_ms()

    started = _meta_int(reader, "collector_started_ms")
    stopped = _meta_int(reader, "collector_stopped_ms")
    last_block = _meta_int(reader, "last_block")
    latency = _meta_float(reader, "last_block_ingest_latency_ms")
    heartbeat = _meta_int(reader, META_HEARTBEAT)
    last_tick = _meta_int(reader, META_LAST_TICK_WRITE)
    # Prefer O(1) meta stamps. Scan only when meta is missing (legacy journal
    # written by a collector binary without WHI-825 last-tick stamp).
    # Annotate as optional: the scan path legitimately returns None (empty journal).
    freshest: int | None
    if last_tick is not None:
        freshest = last_tick
    else:
        freshest = reader.freshest_recv_ts_ms()

    data_age = None if freshest is None else max(0, ts - freshest)
    heartbeat_age = None if heartbeat is None else max(0, ts - heartbeat)

    # Process liveness: heartbeat first, else tick freshest (legacy journals).
    liveness_age = heartbeat_age if heartbeat is not None else data_age
    alive = liveness_age is not None and liveness_age <= stale_ms
    # Clean-shutdown marker: only trust ``collector_stopped_ms`` when nothing
    # fresher has been written afterwards. A later heartbeat / tick means a new
    # process is up (restart race can leave a stale stop after a new start).
    if (
        alive
        and started is not None
        and stopped is not None
        and stopped >= started
    ):
        progressive = max(
            x for x in (heartbeat, freshest) if x is not None
        ) if (heartbeat is not None or freshest is not None) else None
        if progressive is None or progressive <= stopped:
            alive = False

    since = ts - gap_window_ms
    # Source-filter collector_down before LIMIT so block-lag spam cannot hide
    # downtime (WHI-825). Merge into recent_gaps for the wire payload.
    down_gaps = reader.recent_gaps(
        since_ms=since, limit=20, source=SOURCE_COLLECTOR_DOWN
    )
    other_gaps = reader.recent_gaps(since_ms=since, limit=20)
    # Prefer downtime rows first, then other sources, de-dupe by identity.
    seen: set[tuple[str, int, int]] = set()
    gaps: list[CollectorGap] = []
    for g in list(down_gaps) + list(other_gaps):
        key = (g.source, g.gap_start_ms, g.gap_end_ms)
        if key in seen:
            continue
        seen.add(key)
        gaps.append(g)
        if len(gaps) >= 20:
            break
    down_recent = bool(down_gaps)
    feed_state = classify_feed_state(
        collector_alive=alive,
        data_age_ms=data_age,
        quiet_ms=quiet_ms,
        collector_down_gap_recent=down_recent,
    )
    hint = recovery_hint_for_state(
        feed_state=feed_state,
        market_id=market_id,
        age_ms=liveness_age if not alive else data_age,
        collector_down_gap_recent=down_recent,
        recent_gaps=gaps,
    )

    mismatches = mismatches_from_meta_json(reader.get_meta(META_MISMATCHES))
    probe_ms = _meta_int(reader, META_PROBE_MS)
    probe_errors = errors_from_meta_json(reader.get_meta(META_PROBE_ERRORS))
    unpublished = unpublished_from_meta_json(reader.get_meta(META_UNPUBLISHED))

    # WHI-974: RFQ error rate over a short window (readable without journald).
    rfq_cov: RfqQuoteCoverage = rfq_quote_coverage(
        reader, since_ms=max(0, ts - rfq_error_window_ms)
    )
    cex_block = _cex_volume_block_from_meta(reader)

    # ``ok`` is the UI banner aggregate: process alive and not in a hard-down
    # state. feed_quiet / advisory gap keep ok=True so the panel stays usable;
    # feed_down and missing journal flip ok=False.
    ok = alive and feed_state != "feed_down"
    return HealthStatus(
        ok=ok,
        generated_ts_ms=ts,
        db_path=str(reader.path),
        db_exists=True,
        collector_started_ms=started,
        collector_stopped_ms=stopped,
        last_block=last_block,
        last_block_ingest_latency_ms=latency,
        freshest_recv_ts_ms=freshest,
        age_ms=data_age,
        collector_alive=alive,
        gap_recent=bool(gaps),
        recent_gaps=gaps,
        poll_interval_s=poll_interval_s,
        uncovered_coverage_mismatches=mismatches,
        uncovered_coverage_probe_ms=probe_ms,
        uncovered_coverage_probe_errors=probe_errors,
        unpublished_pyth_feeds=unpublished,
        feed_state=feed_state,
        recovery_hint=hint,
        heartbeat_age_ms=heartbeat_age,
        collector_down_gap_recent=down_recent,
        rfq_error_rate=rfq_cov.error_rate,
        rfq_error_rows=rfq_cov.error_rows,
        rfq_total_rows=rfq_cov.total_rows,
        rfq_availability_among_reachable=rfq_cov.availability_among_reachable,
        rfq_http_status_counts=rfq_cov.by_status,
        rfq_coverage_window_ms=rfq_error_window_ms,
        **cex_block,
    )
