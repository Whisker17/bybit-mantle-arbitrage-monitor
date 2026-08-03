"""Seam: process-level write-activity watchdog (WHI-825)."""

from __future__ import annotations

from monitor.collector.watchdog import WatchdogAction, evaluate_watchdog


def _eval(**kwargs: object) -> WatchdogAction:
    defaults: dict[str, object] = {
        "now_ms": 100_000,
        "last_write_ms": 95_000,
        "process_started_ms": 0,
        "reconnect_idle_ms": 90_000,
        "exit_idle_ms": 180_000,
        "startup_grace_ms": 60_000,
        "writes_paused": False,
        "enabled": True,
    }
    defaults.update(kwargs)
    return evaluate_watchdog(**defaults)  # type: ignore[arg-type]


def test_ok_when_recent_write() -> None:
    assert _eval(now_ms=100_000, last_write_ms=95_000) is WatchdogAction.OK


def test_reconnect_after_idle() -> None:
    # 95s idle → past 90s reconnect, under 180s exit
    assert (
        _eval(now_ms=200_000, last_write_ms=105_000, process_started_ms=0)
        is WatchdogAction.RECONNECT
    )


def test_exit_after_long_idle() -> None:
    assert (
        _eval(now_ms=300_000, last_write_ms=100_000, process_started_ms=0)
        is WatchdogAction.EXIT
    )


def test_startup_grace_suppresses() -> None:
    # Process only 30s old; no writes yet — still grace
    assert (
        _eval(
            now_ms=30_000,
            last_write_ms=None,
            process_started_ms=0,
            startup_grace_ms=60_000,
        )
        is WatchdogAction.OK
    )


def test_no_writes_after_grace_exits() -> None:
    assert (
        _eval(
            now_ms=200_000,
            last_write_ms=None,
            process_started_ms=0,
            startup_grace_ms=60_000,
            reconnect_idle_ms=90_000,
            exit_idle_ms=180_000,
        )
        is WatchdogAction.EXIT
    )


def test_writes_paused_is_ok() -> None:
    assert (
        _eval(
            now_ms=500_000,
            last_write_ms=100_000,
            writes_paused=True,
        )
        is WatchdogAction.OK
    )


def test_disabled_is_ok() -> None:
    assert (
        _eval(
            now_ms=500_000,
            last_write_ms=100_000,
            enabled=False,
        )
        is WatchdogAction.OK
    )


def test_does_not_use_tiny_idle_as_dead() -> None:
    """Closed-session quiet of tens of seconds is not a process failure."""
    # 45s idle — under 90s reconnect
    assert (
        _eval(now_ms=145_000, last_write_ms=100_000, reconnect_idle_ms=90_000)
        is WatchdogAction.OK
    )
