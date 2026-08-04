"""Process-level write-activity watchdog (WHI-825).

Detects "alive but not journaling" — WS connected / tasks spinning while no
rows land. Thresholds are **not** per-symbol quote age (quiet closed-session
bookTicker is normal; see WHI-821). Use any successful journal write, or a
meta heartbeat, as the progress signal.

Actions:

* ``ok`` — recent write within reconnect idle
* ``reconnect`` — idle past N → force transport reconnect (CEX WS)
* ``exit`` — idle past M → non-zero process exit; supervisor restarts us
"""

from __future__ import annotations

from enum import StrEnum

# Meta key written by the collector watchdog loop (process liveness for /api/health).
META_HEARTBEAT = "collector_heartbeat_ms"
# Throttled stamp of last tick-table write (book/pool/swap/…). Health uses this
# instead of MAX(recv_ts_ms) over multi-million-row tables (WHI-825 switch lag).
META_LAST_TICK_WRITE = "collector_last_tick_write_ms"
# How often to refresh META_LAST_TICK_WRITE (ms). 1s is fine for health age.
LAST_TICK_META_MIN_INTERVAL_MS = 1000
# WHI-835: last subsystem error so "why did writes stop?" is answerable.
META_LAST_FEED_ERROR = "collector_last_feed_error"
META_LAST_FEED_ERROR_MS = "collector_last_feed_error_ms"
META_LAST_FEED_ERROR_SOURCE = "collector_last_feed_error_source"


class WatchdogAction(StrEnum):
    OK = "ok"
    RECONNECT = "reconnect"
    EXIT = "exit"


def evaluate_watchdog(
    *,
    now_ms: int,
    last_write_ms: int | None,
    process_started_ms: int,
    reconnect_idle_ms: int,
    exit_idle_ms: int,
    startup_grace_ms: int,
    writes_paused: bool = False,
    enabled: bool = True,
) -> WatchdogAction:
    """Decide whether to reconnect or exit based on journal write silence.

    Parameters
    ----------
    last_write_ms
        Wall-clock of the most recent successful journal write (ticks or
        heartbeat). ``None`` means nothing has been written yet this process.
    writes_paused
        Disk-critical book pause (WHI-751): do not treat intentional silence
        as a dead feed.
    """
    if not enabled:
        return WatchdogAction.OK
    if writes_paused:
        return WatchdogAction.OK
    # Thresholds are validated by WatchdogConfig (gt=0, exit >= reconnect).

    if now_ms - process_started_ms < startup_grace_ms:
        return WatchdogAction.OK

    baseline = last_write_ms if last_write_ms is not None else process_started_ms
    idle_ms = now_ms - baseline
    if idle_ms < 0:
        return WatchdogAction.OK
    if idle_ms >= exit_idle_ms:
        return WatchdogAction.EXIT
    if idle_ms >= reconnect_idle_ms:
        return WatchdogAction.RECONNECT
    return WatchdogAction.OK
