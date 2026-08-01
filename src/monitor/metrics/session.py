"""US equity RTH session classifier (NYSE calendar, America/New_York).

DESIGN §2.4: all aggregates split by open vs closed. Includes full holidays and
early-close days so closed-hour stats do not leak half-day afternoons.

Session hours come from ``MetricsConfig.session`` / ``config/metrics.yaml`` —
callers must pass a loaded config (no silent hardcoded defaults).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from monitor.metrics.config import MetricsConfig, SessionConfig

# Fixed NYSE full holidays (no session). Observed Monday/Friday shifts included.
# Years covered: 2025–2027. session_kind raises for other years so the table
# cannot silently expire (acceptance: open/closed matches the calendar).
_CALENDAR_YEARS: frozenset[int] = frozenset({2025, 2026, 2027})

_NYSE_FULL_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2025
        date(2025, 1, 1),  # New Year's Day
        date(2025, 1, 20),  # MLK Day
        date(2025, 2, 17),  # Presidents' Day
        date(2025, 4, 18),  # Good Friday
        date(2025, 5, 26),  # Memorial Day
        date(2025, 6, 19),  # Juneteenth
        date(2025, 7, 4),  # Independence Day
        date(2025, 9, 1),  # Labor Day
        date(2025, 11, 27),  # Thanksgiving
        date(2025, 12, 25),  # Christmas
        # 2026
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # MLK Day
        date(2026, 2, 16),  # Presidents' Day
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),  # Independence Day (observed; Jul 4 is Saturday)
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
        # 2027
        date(2027, 1, 1),  # New Year's Day
        date(2027, 1, 18),  # MLK Day
        date(2027, 2, 15),  # Presidents' Day
        date(2027, 3, 26),  # Good Friday
        date(2027, 5, 31),  # Memorial Day
        date(2027, 6, 18),  # Juneteenth (observed; Jun 19 is Saturday)
        date(2027, 7, 5),  # Independence Day (observed; Jul 4 is Sunday)
        date(2027, 9, 6),  # Labor Day
        date(2027, 11, 25),  # Thanksgiving
        date(2027, 12, 24),  # Christmas (observed; Dec 25 is Saturday)
    }
)

# NYSE early close at 13:00 ET (half days).
_NYSE_EARLY_CLOSE: frozenset[date] = frozenset(
    {
        date(2025, 7, 3),  # Day before Independence Day
        date(2025, 11, 28),  # Day after Thanksgiving
        date(2025, 12, 24),  # Christmas Eve
        date(2026, 11, 27),  # Day after Thanksgiving
        date(2026, 12, 24),  # Christmas Eve
        date(2027, 11, 26),  # Day after Thanksgiving
    }
)


class SessionKind(StrEnum):
    """Coarse session label used for segmented aggregates."""

    OPEN = "open"
    CLOSED = "closed"


def _as_session_config(config: SessionConfig | MetricsConfig) -> SessionConfig:
    if isinstance(config, MetricsConfig):
        return config.session
    return config


def _as_et(ts: datetime, tz: ZoneInfo) -> datetime:
    if ts.tzinfo is None:
        # Interpret naive as UTC wall-clock (collector timestamps are epoch-based).
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(tz)


def nyse_is_full_holiday(d: date) -> bool:
    return d in _NYSE_FULL_HOLIDAYS


def nyse_is_early_close(d: date) -> bool:
    return d in _NYSE_EARLY_CLOSE


def session_kind(
    ts: datetime,
    *,
    config: SessionConfig | MetricsConfig,
) -> SessionKind:
    """Return OPEN during NYSE RTH (incl. early-close mornings), else CLOSED.

    ``config`` is required so hours always come from ``config/metrics.yaml``
    (or an explicit ``SessionConfig``), never a silent hardcode.
    """
    sc = _as_session_config(config)
    et = _as_et(ts, ZoneInfo(sc.timezone))
    d = et.date()
    if d.year not in _CALENDAR_YEARS:
        raise ValueError(
            f"NYSE holiday table covers {_CALENDAR_YEARS}; got {d.year}. "
            "Extend _NYSE_FULL_HOLIDAYS / _NYSE_EARLY_CLOSE in "
            "monitor.metrics.session before classifying this timestamp."
        )
    if et.weekday() >= 5:
        return SessionKind.CLOSED
    if nyse_is_full_holiday(d):
        return SessionKind.CLOSED

    minutes = et.hour * 60 + et.minute
    open_m = sc.open_minutes()
    if nyse_is_early_close(d):
        close_m = sc.early_close_minutes()
    else:
        close_m = sc.close_minutes()

    # Half-open [open, close): 09:30 inclusive, 16:00 exclusive (and 13:00 on
    # early-close days). Matches exchange "last trade" end convention for stats.
    if open_m <= minutes < close_m:
        return SessionKind.OPEN
    return SessionKind.CLOSED


def is_us_rth_open(
    ts: datetime,
    *,
    config: SessionConfig | MetricsConfig,
) -> bool:
    return session_kind(ts, config=config) is SessionKind.OPEN
