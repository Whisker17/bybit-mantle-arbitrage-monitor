"""Process-lifetime app state shared by request handlers."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Request

from monitor.api.config import ApiConfig
from monitor.attribution.config import AttributionConfig
from monitor.metrics.config import MetricsConfig
from monitor.storage import JournalReader
from monitor.symbols.models import PairsConfig
from monitor.tui.config import TuiConfig
from monitor.tui.model import RunningEdgeState


@dataclass
class AppState:
    """Loaded configs + optional reader + running edge stats.

    Sync FastAPI handlers run on Starlette's threadpool, so mutations of
    ``edge_state`` and reader open/reopen take ``lock``.
    """

    api: ApiConfig
    pairs: PairsConfig
    metrics: MetricsConfig
    attribution: AttributionConfig
    tui: TuiConfig
    db_path: Path
    reader: JournalReader | None
    edge_state: RunningEdgeState = field(default_factory=RunningEdgeState)
    lock: threading.Lock = field(default_factory=threading.Lock)

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
