"""TTL cache for PnL v2 snapshots (WHI-766).

Process-local, single-worker safe (API runs one uvicorn worker). Keys are
pair ids; values are full ``PnlPairSnapshot`` objects so overview + detail
share one compute. TTL default is 2.5s (slightly above ``poll_interval_s``)
so a steady poller still hits cache; concurrent overview + detail share one
optimal-size search per pair.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from monitor.metrics.pnl_snapshot import PnlPairSnapshot


@dataclass
class PnlSnapshotCache:
    """Simple wall-clock TTL map. Not thread-safe alone — hold AppState.lock."""

    ttl_s: float
    _entries: dict[str, tuple[float, PnlPairSnapshot]] = field(default_factory=dict)

    def get(self, pair_id: str, *, now: float | None = None) -> PnlPairSnapshot | None:
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
        value: PnlPairSnapshot,
        *,
        now: float | None = None,
    ) -> None:
        if self.ttl_s <= 0:
            return
        ts = time.monotonic() if now is None else now
        self._entries[pair_id] = (ts + self.ttl_s, value)

    def clear(self) -> None:
        self._entries.clear()
