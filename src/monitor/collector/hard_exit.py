"""Hard process exit when graceful shutdown hangs (WHI-835).

Watchdog and supervisors need the process to *actually leave* so a parent
can restart it. ``asyncio.to_thread`` work runs on non-daemon threads in a
``ThreadPoolExecutor``; after clients are closed those threads can spin on
retries for a long time and block interpreter exit after ``SystemExit``.

``arm_hard_exit`` schedules ``os._exit`` on a daemon timer — last resort
that cannot be blocked by non-daemon worker threads.

The armed exit **code** is mutable: a later non-zero arm upgrades a prior
zero (SIGTERM-first race must not force ``os._exit(0)`` after the watchdog
decided to exit 1).
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_armed = False
_exit_code = 0


def reset_hard_exit_for_tests() -> None:
    """Test-only: allow re-arming in the same process."""
    global _armed, _exit_code
    with _lock:
        _armed = False
        _exit_code = 0


def arm_hard_exit(
    *,
    code: int,
    timeout_s: float,
    reason: str = "shutdown hung",
) -> bool:
    """Schedule ``os._exit`` if still alive after ``timeout_s``.

    First call starts the daemon timer. Subsequent calls may **upgrade**
    the exit code to non-zero (watchdog wins over SIGTERM). Returns True
    when a new timer was started, False when only the code/reason updated
    (or no-op when already armed with a non-zero and new code is 0).
    """
    global _armed, _exit_code
    delay = max(0.1, float(timeout_s))
    desired = int(code)

    with _lock:
        # Non-zero always wins; zero only sticks if nothing armed yet / still 0.
        if desired != 0:
            _exit_code = desired
        elif not _armed:
            _exit_code = 0

        if _armed:
            return False
        _armed = True
        start_timer = True
        captured_reason = reason
    # Release lock before starting the timer (Timer is itself thread-safe enough).

    def _fire() -> None:
        with _lock:
            final = _exit_code
        try:
            logger.error(
                "hard exit: %s after %.1fs — os._exit(%s)",
                captured_reason,
                delay,
                final,
            )
        except Exception:  # noqa: BLE001 - last gasp, never raise
            pass
        os._exit(final)

    if start_timer:
        timer = threading.Timer(delay, _fire)
        timer.daemon = True
        timer.name = "collector-hard-exit"
        timer.start()
        logger.warning(
            "hard exit armed: code=%s timeout_s=%.1f reason=%s",
            desired,
            delay,
            reason,
        )
        return True
    return False
