"""TTL cache for capture-rate snapshots (WHI-963).

Process-local, single-worker safe (same assumptions as ``pnl_cache``). Capture
looks back over hours of journal ticks and is far heavier than a PnL optimal
search, so the default TTL is longer (30s) — still well under the "how much
could a night have earned" freshness need, and keeps overview P95 inside the
DESIGN §2.6.7 budget when the cache is warm.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from monitor.metrics.capture import CapturePairSnapshot


@dataclass
class CaptureSnapshotCache:
    """Wall-clock TTL map keyed by pair id. Hold ``MarketRuntime.lock``."""

    ttl_s: float
    _entries: dict[str, tuple[float, CapturePairSnapshot]] = field(
        default_factory=dict
    )

    def get(
        self, pair_id: str, *, now: float | None = None
    ) -> CapturePairSnapshot | None:
        if self.ttl_s <= 0:
            return None
        ts = time.monotonic() if now is None else now
        hit = self._entries.get(pair_id)
        if hit is None:
            return None
        expires_at, value = hit
        if ts >= expires_at:
            del self._entries[pair_id]
            return None
        return value

    def put(
        self,
        pair_id: str,
        value: CapturePairSnapshot,
        *,
        now: float | None = None,
    ) -> None:
        if self.ttl_s <= 0:
            return
        ts = time.monotonic() if now is None else now
        self._entries[pair_id] = (ts + self.ttl_s, value)

    def clear(self) -> None:
        self._entries.clear()
