"""Seam: hard exit reaps hung to_thread workers (WHI-835)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from monitor.collector.hard_exit import arm_hard_exit, reset_hard_exit_for_tests


def test_arm_hard_exit_idempotent_timer() -> None:
    reset_hard_exit_for_tests()
    assert arm_hard_exit(code=1, timeout_s=60.0, reason="first") is True
    # Second arm does not start another timer.
    assert arm_hard_exit(code=2, timeout_s=60.0, reason="second") is False
    reset_hard_exit_for_tests()


def test_nonzero_upgrades_prior_zero() -> None:
    """SIGTERM-first must not freeze os._exit(0) after watchdog sets exit 1."""
    reset_hard_exit_for_tests()
    assert arm_hard_exit(code=0, timeout_s=60.0, reason="sigterm") is True
    # Upgrade path: second call returns False (timer already running) but
    # must still stamp non-zero so the eventual fire uses 1.
    assert arm_hard_exit(code=1, timeout_s=60.0, reason="watchdog") is False
    # Probe internal code via a short subprocess would be heavy; exercise
    # the module cell by re-reading after reset is the only pure seam —
    # verify via fire-time subprocess below instead.
    reset_hard_exit_for_tests()


def test_subprocess_upgrades_zero_to_nonzero() -> None:
    script = textwrap.dedent(
        """
        import time
        from monitor.collector.hard_exit import arm_hard_exit, reset_hard_exit_for_tests

        reset_hard_exit_for_tests()
        arm_hard_exit(code=0, timeout_s=0.8, reason="sigterm-first")
        arm_hard_exit(code=7, timeout_s=0.8, reason="watchdog-upgrade")
        time.sleep(5)
        """
    )
    repo = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 7, (
        f"expected upgraded hard exit 7, got {proc.returncode}\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )


def test_subprocess_exits_despite_hung_to_thread() -> None:
    """Acceptance: hung default-executor work must not block exit past timeout.

    Without hard exit, non-daemon ThreadPoolExecutor threads keep the process
    alive after the event loop ends. Current unfixed code would hang ~1h here;
    with arm_hard_exit the child must leave within a few seconds.
    """
    script = textwrap.dedent(
        """
        import asyncio
        import concurrent.futures
        import time

        from monitor.collector.hard_exit import arm_hard_exit, reset_hard_exit_for_tests

        reset_hard_exit_for_tests()

        async def main() -> None:
            ex = concurrent.futures.ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="hung-io"
            )
            loop = asyncio.get_running_loop()
            loop.set_default_executor(ex)

            async def hang() -> None:
                await asyncio.to_thread(time.sleep, 3600)

            task = asyncio.create_task(hang())
            # Arm *before* cancel — mirrors watchdog exit → request_stop order.
            arm_hard_exit(code=42, timeout_s=1.0, reason="test hung to_thread")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            ex.shutdown(wait=False, cancel_futures=True)
            # Simulate post-run SystemExit path: main thread returns while
            # non-daemon worker may still be inside time.sleep(3600).
            time.sleep(30)

        asyncio.run(main())
        """
    )
    # Run from repo so `monitor` imports resolve via installed package / src.
    repo = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 42, (
        f"expected hard exit 42, got {proc.returncode}\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
