"""HTTP client error classification (WHI-835).

Closed/unavailable clients must not enter multi-attempt retry loops — every
attempt fails the same way and only stretches a zombie shutdown.
"""

from __future__ import annotations


def is_non_retryable_client_error(exc: BaseException) -> bool:
    """True when retrying the same client cannot succeed.

    Matches httpx's closed-client ``RuntimeError`` and similar permanent
    transport failures. Transient network errors must return False.
    """
    msg = str(exc).lower()
    if "client has been closed" in msg:
        return True
    if "cannot send a request" in msg and "closed" in msg:
        return True
    # httpx / httpcore variants ("connection pool is closed" ⊆ "pool is closed")
    if "pool is closed" in msg:
        return True
    if isinstance(exc, RuntimeError) and "closed" in msg and "client" in msg:
        return True
    return False
