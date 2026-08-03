"""Process-lifetime app state shared by request handlers (multi-market)."""

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
from monitor.markets.ids import normalize_market_id
from monitor.metrics.config import MetricsConfig
from monitor.storage import JournalReader
from monitor.symbols.bstocks_models import BStocksPairsConfig
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
class MarketRuntime:
    """Per-market journal reader + builder configs (M7-5 / WHI-774).

    One runtime per ``config/markets/{id}.yaml``. Readers open independently
    so a missing/corrupt journal on one market cannot block the other.
    """

    market_id: str
    display_name: str
    has_rfq: bool
    cex_venue: str
    dex_venue: str
    pairs: PairsConfig | None
    pair_count: int
    metrics: MetricsConfig
    attribution: AttributionConfig
    tui: TuiConfig
    db_path: Path
    reader: JournalReader | None
    # Binance ⇄ Pancake inventory (M7). When set, overview/detail builders use
    # BStocksPair paths; ``pairs`` stays None for this market.
    bstocks: BStocksPairsConfig | None = None
    # From market dex.quote_decimals (USDC=6 / USDT=18) — never hardcode at call sites.
    quote_decimals: int = 6
    # WHI-821: optional market-file override of api.yaml quote_max_age_ms.
    quote_max_age_ms: int | None = None
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

    def data_status(self) -> str:
        """Wire status for overview / markets list.

        * ``ok`` — builder-ready inventory (PairsConfig or BStocksPairsConfig)
        * ``accumulating`` — no inventory shape that overview builders accept

        Missing journals on a builder-ready market are an error path (HTTP
        503 on pair routes), not ``accumulating``.
        """
        if self.pairs is None and self.bstocks is None:
            return "accumulating"
        return "ok"

    def inventory_pairs(self) -> PairsConfig | BStocksPairsConfig | None:
        """Return the wired inventory root, if any."""
        if self.pairs is not None:
            return self.pairs
        return self.bstocks


@dataclass
class AppState:
    """Multi-market process state for the read-only API (WHI-774).

    Sync FastAPI handlers run on Starlette's threadpool. Each
    ``MarketRuntime.lock`` serializes builder calls that mutate that market's
    ``edge_state`` *and* reader open/reopen — concurrent polls queue per market
    rather than interleave EdgeStats updates.
    """

    api: ApiConfig
    tui: TuiConfig
    markets: dict[str, MarketRuntime]
    default_market_id: str

    def market(self, market_id: str | None = None) -> MarketRuntime:
        """Resolve a market runtime; default is the process default market."""
        mid = normalize_market_id(market_id or self.default_market_id)
        return self.markets[mid]

    def close(self) -> None:
        for runtime in self.markets.values():
            runtime.close()


def app_state_from_request(request: Request) -> AppState:
    """FastAPI dependency helper — single access path for routes."""
    return request.app.state.app_state  # type: ignore[no-any-return]
