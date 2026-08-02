"""Classify underlying prints as live / pre / post / close / stale (WHI-778)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

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
      unless the *source* explicitly hints pre/post (Yahoo marketState).
      Never invent pre/post from wall-clock alone — Pyth freezes
      ``publish_time`` at the last RTH print, so weekday 16:00–20:00 ET
      would otherwise stamp Friday's close as ``post``.
    * Inside RTH at *now*: fresh as_of during open → **live**;
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
        # Source-explicit session only — never invent pre/post from wall-clock
        # (Pyth freezes as_of at last RTH print). Honor live too: KRX (SKHY)
        # trades while NYSE is closed.
        if source_session_hint in ("pre", "post"):
            return source_session_hint
        if source_session_hint == "live" and age_ms <= stale_after_open_ms:
            return "live"
        return "close"

    # RTH open now.
    if age_ms > stale_after_open_ms:
        return "stale"
    if source_session_hint in ("pre", "post"):
        # Source claims extended hours while we think RTH — keep live if fresh.
        return "live"
    if source_session_hint == "live":
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
