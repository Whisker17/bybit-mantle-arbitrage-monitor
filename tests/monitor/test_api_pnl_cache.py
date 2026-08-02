"""Seam: PnlSnapshotCache TTL get/put/expiry (WHI-766)."""

from __future__ import annotations

from monitor.api.pnl_cache import PnlSnapshotCache
from monitor.metrics.pnl_snapshot import PnlOptimalSummary, PnlPairSnapshot


def _snap(status: str = "ok") -> PnlPairSnapshot:
    best = PnlOptimalSummary(status=status, has_depth=status == "ok")  # type: ignore[arg-type]
    return PnlPairSnapshot(
        status=status,  # type: ignore[arg-type]
        has_depth=status == "ok",
        best=best,
        tables={},
    )


def test_cache_disabled_when_ttl_zero() -> None:
    c = PnlSnapshotCache(ttl_s=0)
    c.put("AAPLx", _snap())
    assert c.get("AAPLx") is None


def test_cache_hit_within_ttl() -> None:
    c = PnlSnapshotCache(ttl_s=2.5)
    snap = _snap()
    c.put("AAPLx", snap, now=100.0)
    assert c.get("AAPLx", now=101.0) is snap


def test_cache_miss_after_ttl() -> None:
    c = PnlSnapshotCache(ttl_s=2.5)
    c.put("AAPLx", _snap(), now=100.0)
    assert c.get("AAPLx", now=102.6) is None
    # Second get still miss (entry deleted)
    assert c.get("AAPLx", now=102.7) is None


def test_cache_clear() -> None:
    c = PnlSnapshotCache(ttl_s=10)
    c.put("AAPLx", _snap(), now=0.0)
    c.clear()
    assert c.get("AAPLx", now=0.0) is None
