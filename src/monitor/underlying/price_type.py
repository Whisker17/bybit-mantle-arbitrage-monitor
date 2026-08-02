"""Classify underlying prints as live / pre / post / close / stale (WHI-778)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from monitor.metrics.config import SessionConfig
from monitor.metrics.session import SessionKind, session_kind

PriceType = Literal["live", "pre", "post", "close", "stale"]


def classify_price_type(
    *,
    as_of_ms: int,
    now_ms: int,
    session: SessionConfig,
    stale_after_open_ms: int,
    stale_after_closed_ms: int,
    stale_after_abs_ms: int,
    source_session_hint: PriceType | None = None,
) -> PriceType:
    """Label a source print for safe premium math.

    Rules (see docs/references/underlying-price-source.md):

    * Absolute age > ``stale_after_abs_ms`` → **stale** (dead feed).
    * Outside NYSE RTH at *now*: if age ≤ closed window → **close**
      (or pre/post if the source hints extended hours); else **stale**.
    * Inside RTH at *now*: fresh as_of → **live** (or pre/post hint);
      older than open window → **stale**.
    """
    if now_ms < as_of_ms:
        # Clock skew — treat as fresh relative to now.
        age_ms = 0
    else:
        age_ms = now_ms - as_of_ms

    if age_ms > stale_after_abs_ms:
        return "stale"

    now_dt = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
    as_of_dt = datetime.fromtimestamp(as_of_ms / 1000, tz=UTC)

    try:
        now_kind = session_kind(now_dt, config=session)
    except ValueError:
        # Holiday table year gap — prefer stale over mislabeling live.
        return "stale"

    if now_kind is SessionKind.CLOSED:
        if age_ms > stale_after_closed_ms:
            return "stale"
        if source_session_hint in ("pre", "post"):
            return source_session_hint
        # Prefer close over pre/post inference when market is fully closed
        # (weekend / holiday). Extended-hours only when *now* is a trading day
        # outside RTH.
        tz = ZoneInfo(session.timezone)
        et = now_dt.astimezone(tz)
        if et.weekday() < 5 and _is_trading_day(et.date(), session):
            minutes = et.hour * 60 + et.minute
            open_m = session.open_minutes()
            close_m = (
                session.early_close_minutes()
                if _is_early_close(et.date())
                else session.close_minutes()
            )
            if 4 * 60 <= minutes < open_m:
                return "pre"
            if close_m <= minutes < 20 * 60:
                return "post"
        return "close"

    # RTH open now.
    if age_ms > stale_after_open_ms:
        return "stale"
    if source_session_hint in ("pre", "post"):
        # Source claims extended hours while we think RTH — keep live if fresh.
        return "live"
    # as_of should itself fall in open for a true live print; if as_of is from
    # prior close but age is somehow still small (clock jump), mark close.
    try:
        as_kind = session_kind(as_of_dt, config=session)
    except ValueError:
        return "stale"
    if as_kind is SessionKind.CLOSED:
        return "close"
    return "live"


def _is_trading_day(d: object, session: SessionConfig) -> bool:
    from datetime import date as date_cls

    from monitor.metrics.session import nyse_is_full_holiday

    if not isinstance(d, date_cls):
        return False
    if d.weekday() >= 5:
        return False
    return not nyse_is_full_holiday(d)


def _is_early_close(d: object) -> bool:
    from datetime import date as date_cls

    from monitor.metrics.session import nyse_is_early_close

    return isinstance(d, date_cls) and nyse_is_early_close(d)
