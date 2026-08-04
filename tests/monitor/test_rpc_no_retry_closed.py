"""Seam: Rpc must not retry a closed httpx client (WHI-835)."""

from __future__ import annotations

import time

import httpx
import pytest

from monitor.fluxion.rpc import Rpc, RpcError


def test_closed_client_fails_fast_without_backoff_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(float(s)))

    rpc = Rpc("https://example.invalid/rpc", retries=5, min_interval=0.0)
    rpc.close()  # close owned client before any post

    t0 = time.monotonic()
    with pytest.raises(RpcError, match="client has been closed|giving up"):
        rpc.call("eth_blockNumber", [])
    elapsed = time.monotonic() - t0

    # No exponential backoff sleeps for permanent closed-client failures.
    assert sleeps == []
    assert elapsed < 1.0


def test_closed_injected_client_fails_fast() -> None:
    client = httpx.Client()
    client.close()
    rpc = Rpc("https://example.invalid/rpc", retries=5, min_interval=0.0)
    # Replace transport with already-closed client.
    rpc._client = client  # noqa: SLF001 - intentional seam for closed-state test

    with pytest.raises(RpcError):
        rpc.call("eth_blockNumber", [])
