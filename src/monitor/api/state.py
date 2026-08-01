"""Process-lifetime app state shared by request handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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

    The reader is opened at startup when the journal exists; if the collector
    has not created the DB yet, routes that need data return 503 / empty health
    until restart (operator starts collector first in normal deploy).
    """

    api: ApiConfig
    pairs: PairsConfig
    metrics: MetricsConfig
    attribution: AttributionConfig
    tui: TuiConfig
    db_path: Path
    reader: JournalReader | None
    edge_state: RunningEdgeState = field(default_factory=RunningEdgeState)

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()
            self.reader = None
