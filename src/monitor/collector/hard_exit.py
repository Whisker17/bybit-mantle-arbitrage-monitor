"""Hard process exit when graceful shutdown hangs (WHI-835).

Watchdog and supervisors need the process to *actually leave* so a parent
can restart it. ``asyncio.to_thread`` work runs on non-daemon threads in a
``ThreadPoolExecutor``; after clients are closed those threads can spin on
retries for a long time and block interpreter exit after ``SystemExit``.

``arm_hard_exit`` schedules ``os._exit`` on a daemon timer — last resort
that cannot be blocked by non-daemon worker threads.
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_armed = False


def reset_hard_exit_for_tests() -> None:
    """Test-only: allow re-arming in the same process."""
    global _armed
    with _lock:
        _armed = False


def arm_hard_exit(
    *,
    code: int,
    timeout_s: float,
    reason: str = "shutdown hung",
) -> bool:
    """Schedule ``os._exit(code)`` if still alive after ``timeout_s``.

    Idempotent: only the first arm in a process wins. Returns True when a
    new timer was started, False when already armed.

    The timer thread is a daemon so it never keeps the process alive by
    itself; ``os._exit`` is the path that reaps hung non-daemon workers.
    """
    global _armed
    with _lock:
        if _armed:
            return False
        _armed = True

    delay = max(0.1, float(timeout_s))
    exit_code = int(code)

    def _fire() -> None:
        try:
            logger.error(
                "hard exit: %s after %.1fs — os._exit(%s)",
                reason,
                delay,
                exit_code,
            )
        except Exception:  # noqa: BLE001 - last gasp, never raise
            pass
        os._exit(exit_code)

    timer = threading.Timer(delay, _fire)
    timer.daemon = True
    timer.name = "collector-hard-exit"
    timer.start()
    logger.warning(
        "hard exit armed: code=%s timeout_s=%.1f reason=%s",
        exit_code,
        delay,
        reason,
    )
    return True
