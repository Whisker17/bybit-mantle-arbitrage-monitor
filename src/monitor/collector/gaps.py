"""Collector downtime gap accounting (WHI-825).

When the process restarts after a stop/crash/kill, the wall-clock hole between
the last journal write and the first write of the new process must be recorded
as ``collector_gaps.source=collector_down``. Downstream cumulative stats treat
that interval as excluded (zero weight) so P50/P95/P99 are not silently polluted
by "market was quiet" assumptions.
"""

from __future__ import annotations

from collections.abc import Sequence

from monitor.quotes import CollectorGap

SOURCE_COLLECTOR_DOWN = "collector_down"

# Skip routine bounce noise (config reload / brief stop). Real outages are
# minutes+; 30s still captures short kill-restart cycles for ops visibility.
DEFAULT_MIN_COLLECTOR_DOWN_MS = 30_000


def collector_down_gap(
    *,
    last_write_ms: int,
    first_write_ms: int,
    min_gap_ms: int = DEFAULT_MIN_COLLECTOR_DOWN_MS,
    market_id: str | None = None,
) -> CollectorGap | None:
    """Build a ``collector_down`` gap for ``[last_write, first_write]`` or None.

    Returns None when the interval is non-positive or shorter than ``min_gap_ms``.
    """
    if first_write_ms <= last_write_ms:
        return None
    duration = first_write_ms - last_write_ms
    if duration < min_gap_ms:
        return None
    detail = f"collector process down duration_ms={duration}"
    if market_id:
        detail = f"market={market_id} {detail}"
    return CollectorGap(
        source=SOURCE_COLLECTOR_DOWN,
        gap_start_ms=last_write_ms,
        gap_end_ms=first_write_ms,
        detail=detail,
    )


def interval_overlaps_gaps(
    prev_ts_ms: int,
    ts_ms: int,
    gaps: Sequence[tuple[int, int]],
) -> bool:
    """True when ``(prev_ts_ms, ts_ms]`` overlaps any ``[start, end]`` gap."""
    if ts_ms <= prev_ts_ms:
        return False
    for start, end in gaps:
        if prev_ts_ms < end and ts_ms > start:
            return True
    return False


def inter_sample_weight_ms(
    prev_ts_ms: int,
    ts_ms: int,
    *,
    max_gap_ms: int,
    exclude_intervals: Sequence[tuple[int, int]] | None = None,
) -> int:
    """Forward weight for cumulative distributions (DESIGN §2.4 + WHI-825).

    Zero weight when:

    * non-positive interval
    * raw interval exceeds ``max_gap_ms`` (overnight / reconnect cap)
    * interval overlaps a known exclude window (e.g. ``collector_down``)
    """
    raw = ts_ms - prev_ts_ms
    if raw <= 0:
        return 0
    if raw > max_gap_ms:
        return 0
    if exclude_intervals and interval_overlaps_gaps(
        prev_ts_ms, ts_ms, exclude_intervals
    ):
        return 0
    return raw
