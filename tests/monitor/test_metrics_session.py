"""Seam: is_us_rth_open / session_kind vs NYSE calendar."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from monitor.metrics import SessionKind, is_us_rth_open, load_metrics_config, session_kind
from monitor.metrics.session import nyse_is_early_close, nyse_is_full_holiday

ET = ZoneInfo("America/New_York")
CFG = load_metrics_config()


def _et(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_weekday_rth_open_and_closed() -> None:
    # 2026-08-03 is a Monday, not a holiday.
    assert session_kind(_et(2026, 8, 3, 9, 30), config=CFG) is SessionKind.OPEN
    assert session_kind(_et(2026, 8, 3, 12, 0), config=CFG) is SessionKind.OPEN
    assert session_kind(_et(2026, 8, 3, 15, 59), config=CFG) is SessionKind.OPEN
    # 16:00 exclusive
    assert session_kind(_et(2026, 8, 3, 16, 0), config=CFG) is SessionKind.CLOSED
    assert session_kind(_et(2026, 8, 3, 9, 29), config=CFG) is SessionKind.CLOSED
    assert is_us_rth_open(_et(2026, 8, 3, 10, 0), config=CFG)
    assert not is_us_rth_open(_et(2026, 8, 3, 18, 0), config=CFG)


def test_weekend_closed() -> None:
    assert session_kind(_et(2026, 8, 1, 12, 0), config=CFG) is SessionKind.CLOSED
    assert session_kind(_et(2026, 8, 2, 12, 0), config=CFG) is SessionKind.CLOSED


def test_full_holiday_closed() -> None:
    assert nyse_is_full_holiday(datetime(2026, 7, 3).date())
    assert session_kind(_et(2026, 7, 3, 11, 0), config=CFG) is SessionKind.CLOSED
    assert session_kind(_et(2026, 11, 26, 10, 0), config=CFG) is SessionKind.CLOSED
    assert session_kind(_et(2025, 12, 25, 12, 0), config=CFG) is SessionKind.CLOSED


def test_early_close_day() -> None:
    assert nyse_is_early_close(datetime(2026, 11, 27).date())
    assert session_kind(_et(2026, 11, 27, 10, 0), config=CFG) is SessionKind.OPEN
    assert session_kind(_et(2026, 11, 27, 12, 59), config=CFG) is SessionKind.OPEN
    assert session_kind(_et(2026, 11, 27, 13, 0), config=CFG) is SessionKind.CLOSED
    assert session_kind(_et(2026, 11, 27, 15, 0), config=CFG) is SessionKind.CLOSED


def test_utc_input_converts_to_et() -> None:
    ts = datetime(2026, 8, 3, 14, 30, tzinfo=UTC)
    assert session_kind(ts, config=CFG) is SessionKind.OPEN
    ts2 = datetime(2026, 8, 3, 13, 29, tzinfo=UTC)
    assert session_kind(ts2, config=CFG) is SessionKind.CLOSED


def test_naive_datetime_treated_as_utc() -> None:
    ts = datetime(2026, 8, 3, 14, 30)
    assert session_kind(ts, config=CFG) is SessionKind.OPEN
