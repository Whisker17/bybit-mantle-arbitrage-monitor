"""Seam: CaptureSnapshotCache TTL (WHI-963)."""

from __future__ import annotations

from decimal import Decimal

from monitor.api.capture_cache import CaptureSnapshotCache
from monitor.metrics.capture import CapturePairSnapshot


def _snap(pair_id: str = "AAPLx") -> CapturePairSnapshot:
    return CapturePairSnapshot(
        status="ok",
        pair_id=pair_id,
        lookback_ms=86_400_000,
        span_ms=86_400_000,
        since_ms=0,
        until_ms=86_400_000,
        trade_duration_ms=390_000,
        reentry_cooldown_ms=420_000,
        size_usd=Decimal(1000),
        windows_per_day=2.0,
        capturable_usd_per_day=Decimal("5"),
        n_windows=2,
        direction="buy_fluxion_sell_bybit",
        session="open",
        venue="amm",
    )


def test_cache_hit_within_ttl() -> None:
    cache = CaptureSnapshotCache(ttl_s=10.0)
    cache.put("AAPLx", _snap(), now=100.0)
    hit = cache.get("AAPLx", now=105.0)
    assert hit is not None
    assert hit.capturable_usd_per_day == Decimal("5")


def test_cache_miss_after_ttl() -> None:
    cache = CaptureSnapshotCache(ttl_s=2.0)
    cache.put("AAPLx", _snap(), now=100.0)
    assert cache.get("AAPLx", now=103.0) is None


def test_ttl_zero_disables() -> None:
    cache = CaptureSnapshotCache(ttl_s=0)
    cache.put("AAPLx", _snap(), now=100.0)
    assert cache.get("AAPLx", now=100.0) is None
