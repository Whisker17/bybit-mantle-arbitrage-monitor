"""Process-lifetime app state shared by request handlers."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Request

from monitor.api.config import ApiConfig
from monitor.api.pnl_cache import PnlSnapshotCache
from monitor.attribution.config import AttributionConfig
from monitor.attribution.mm_draft import InventoryEvent
from monitor.metrics.config import MetricsConfig
from monitor.storage import JournalReader
from monitor.symbols.models import PairsConfig
from monitor.tui.config import TuiConfig
from monitor.tui.model import RunningEdgeState


@dataclass
class InventoryEventsCache:
    """Process-local TTL for MM inventory ledger events (WHI-769)."""

    ttl_s: float
    _events: list[InventoryEvent] | None = field(default=None, repr=False)
    _expires_at: float = field(default=0.0, repr=False)

    def get(self) -> list[InventoryEvent] | None:
        if self.ttl_s <= 0 or self._events is None:
            return None
        if time.monotonic() >= self._expires_at:
            self._events = None
            return None
        return self._events

    def put(self, events: list[InventoryEvent]) -> None:
        if self.ttl_s <= 0:
            return
        self._events = events
        self._expires_at = time.monotonic() + self.ttl_s


@dataclass
class AppState:
    """Loaded configs + optional reader + running edge stats.

    Sync FastAPI handlers run on Starlette's threadpool. ``lock`` serializes
    builder calls that mutate ``edge_state`` *and* reader open/reopen — so
    concurrent polls queue rather than interleave EdgeStats updates. Acceptable
    for the 2s client poll + single-operator VPS; split later if latency shows.
    """

    api: ApiConfig
    pairs: PairsConfig
    metrics: MetricsConfig
    attribution: AttributionConfig
    tui: TuiConfig
    db_path: Path
    reader: JournalReader | None
    # From market dex.quote_decimals (USDC=6 / USDT=18) — never hardcode at call sites.
    quote_decimals: int = 6
    edge_state: RunningEdgeState = field(default_factory=RunningEdgeState)
    lock: threading.Lock = field(default_factory=threading.Lock)
    pnl_cache: PnlSnapshotCache | None = None
    inventory_cache: InventoryEventsCache | None = None

    def ensure_reader(self) -> JournalReader | None:
        """Open the journal if it appeared after process start (collector race)."""
        with self.lock:
            if self.reader is not None:
                return self.reader
            if not self.db_path.is_file():
                return None
            self.reader = JournalReader(self.db_path)
            return self.reader

    def close(self) -> None:
        with self.lock:
            if self.reader is not None:
                self.reader.close()
                self.reader = None


def app_state_from_request(request: Request) -> AppState:
    """FastAPI dependency helper — single access path for routes."""
    return request.app.state.app_state  # type: ignore[no-any-return]
