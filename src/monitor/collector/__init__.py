"""Live collector daemon: Bybit WS + Fluxion chain + RFQ → SQLite (M2)."""

from monitor.collector.config import CollectorConfig, load_collector_config

__all__ = ["CollectorConfig", "load_collector_config"]
