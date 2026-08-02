"""Market id constants and SQLite path convention (WHI-771 / ADR-0001)."""

from __future__ import annotations

# Default market = original Bybit ⇄ Fluxion xStocks panel. CLI / config defaults
# keep this so existing ops scripts stay one-flag-free.
DEFAULT_MARKET_ID = "bybit-fluxion"

# Pre-M7-2 single journal path. resolve_market_sqlite may fall back here when
# the per-market file is missing so a live VPS journal is not orphaned on deploy.
LEGACY_SQLITE_RELPATH = "data/monitor.db"

def known_market_ids() -> frozenset[str]:
    """Market ids that have a checked-in ``config/markets/{id}.yaml``.

    Derived from disk (not a hand list) so adding a market is config-only.
    """
    from monitor.markets.load import list_market_ids

    return frozenset(list_market_ids())


def normalize_market_id(raw: str) -> str:
    """Normalize user/CLI market id (underscore → hyphen, lower-case)."""
    mid = raw.strip().lower().replace("_", "-")
    if not mid:
        raise ValueError("market id must be non-empty")
    return mid


def market_sqlite_relpath(market_id: str) -> str:
    """Relative journal path: ``data/monitor-{market_id}.db`` (ADR-0001)."""
    mid = normalize_market_id(market_id)
    return f"data/monitor-{mid}.db"
