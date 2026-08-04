"""Seam: non-retryable closed-client detection (WHI-835)."""

from __future__ import annotations

import httpx

from monitor.fluxion.http_errors import is_non_retryable_client_error


def test_closed_httpx_client_is_non_retryable() -> None:
    client = httpx.Client()
    client.close()
    try:
        client.get("https://example.invalid/")
    except Exception as exc:  # noqa: BLE001 - capture real closed-client error
        assert is_non_retryable_client_error(exc) is True
        return
    raise AssertionError("closed client should raise")


def test_closed_client_message_variants() -> None:
    assert is_non_retryable_client_error(
        RuntimeError("Cannot send a request, as the client has been closed.")
    )
    assert is_non_retryable_client_error(
        RuntimeError("connection pool is closed")
    )


def test_transient_errors_are_retryable() -> None:
    assert is_non_retryable_client_error(TimeoutError("timed out")) is False
    assert is_non_retryable_client_error(ConnectionError("reset by peer")) is False
    assert is_non_retryable_client_error(RuntimeError("something else")) is False
