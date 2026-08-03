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
    if reconnect_idle_ms <= 0 or exit_idle_ms <= 0:
        return WatchdogAction.OK
    if exit_idle_ms < reconnect_idle_ms:
        # Misconfig: treat as exit-only at the larger bound.
        reconnect_idle_ms = exit_idle_ms

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
