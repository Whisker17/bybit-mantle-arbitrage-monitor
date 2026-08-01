"""Collector health snapshot for GET /api/health (pure over JournalReader)."""

from __future__ import annotations

from dataclasses import dataclass

from monitor.quotes import CollectorGap, now_ms
from monitor.storage import JournalReader


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

    @classmethod
    def unavailable(
        cls,
        *,
        db_path: str,
        now: int | None = None,
        poll_interval_s: float | None = None,
        error: str,
    ) -> HealthStatus:
        """Health when the journal file is missing or unreadable."""
        ts = now if now is not None else now_ms()
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


def build_health(
    reader: JournalReader,
    *,
    now: int | None = None,
    stale_ms: int,
    gap_window_ms: int,
    poll_interval_s: float | None = None,
) -> HealthStatus:
    """Derive health from meta keys + freshest journal recv timestamps.

    ``collector_alive`` is true when the freshest recv is within ``stale_ms`` and
    the process has not written ``collector_stopped_ms`` after the last start.
    """
    ts = now if now is not None else now_ms()

    started = _meta_int(reader, "collector_started_ms")
    stopped = _meta_int(reader, "collector_stopped_ms")
    last_block = _meta_int(reader, "last_block")
    latency = _meta_float(reader, "last_block_ingest_latency_ms")
    freshest = reader.freshest_recv_ts_ms()
    age = None if freshest is None else max(0, ts - freshest)
    alive = freshest is not None and age is not None and age <= stale_ms
    # If collector wrote a stop timestamp after start, treat as down even if
    # residual rows still look fresh (edge case on clean shutdown).
    if started is not None and stopped is not None and stopped >= started:
        alive = False

    since = ts - gap_window_ms
    gaps = reader.recent_gaps(since_ms=since, limit=20)
    return HealthStatus(
        ok=alive,
        generated_ts_ms=ts,
        db_path=str(reader.path),
        db_exists=True,
        collector_started_ms=started,
        collector_stopped_ms=stopped,
        last_block=last_block,
        last_block_ingest_latency_ms=latency,
        freshest_recv_ts_ms=freshest,
        age_ms=age,
        collector_alive=alive,
        gap_recent=bool(gaps),
        recent_gaps=gaps,
        poll_interval_s=poll_interval_s,
    )
