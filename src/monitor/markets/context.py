"""MarketContext — assembled runtime view for one market (WHI-771)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from monitor.collector.config import (
    CollectorConfig,
    load_collector_config,
)
from monitor.markets.ids import (
    DEFAULT_MARKET_ID,
    LEGACY_SQLITE_RELPATH,
    market_sqlite_relpath,
    normalize_market_id,
)
from monitor.markets.load import MarketConfigError, load_market_file, market_file_path
from monitor.markets.models import CexSide, DexSide, MarketCosts, MarketFile
from monitor.metrics.config import MetricsConfig, load_metrics_config
from monitor.symbols.models import PairsConfig

# Re-export for tests that import apply_market_costs from monitor.markets
__all__ = [
    "MarketContext",
    "apply_market_costs",
    "load_market_context",
    "resolve_market_sqlite",
]

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True, slots=True)
class MarketContext:
    """Everything assembly code needs for one market process / API surface.

    * ``pairs`` — Bybit⇄Fluxion ``PairsConfig`` when this market uses that
      inventory shape; ``None`` for markets with a different pair schema
      (binance-pancake until M7-3 wires a collector/domain path).
    * ``metrics`` — base metrics.yaml with **venue costs overridden** from the
      market file (fee / gas / quote basis).
    * ``collector`` — market-scoped collector section (bybit-fluxion only today).
    * ``sqlite_path`` — resolved absolute path to this market's journal.
    """

    market_id: str
    display_name: str
    cex: CexSide
    dex: DexSide
    costs: MarketCosts
    market_file: MarketFile
    pairs: PairsConfig | None
    metrics: MetricsConfig
    collector: CollectorConfig | None
    sqlite_path: Path
    inventory_path: Path


def apply_market_costs(metrics: MetricsConfig, costs: MarketCosts) -> MetricsConfig:
    """Return a copy of metrics with venue costs taken from the market file.

    Pure: no I/O. Maps market ``cex_taker_fee_bps`` → ``bybit_taker_fee_bps``
    and ``quote_basis_bps`` → ``usdt_usdc_basis_bps`` so PnL/edge math is
    unchanged while the *source* of the numbers is the market.
    """
    return metrics.model_copy(
        update={
            "bybit_taker_fee_bps": costs.cex_taker_fee_bps,
            "gas_usd_per_swap": costs.gas_usd_per_swap,
            "usdt_usdc_basis_bps": costs.quote_basis_bps,
        }
    )


def resolve_market_sqlite(
    *,
    market_id: str,
    configured: Path,
    repo_root: Path | None = None,
    allow_legacy_fallback: bool = True,
) -> Path:
    """Resolve the journal path for a market (ADR-0001).

    Prefer the configured path. For the default market only, if the configured
    file is missing but legacy ``data/monitor.db`` exists, use the legacy path
    so a VPS journal is not orphaned mid-migration.
    """
    mid = normalize_market_id(market_id)
    if configured.is_file():
        return configured
    if (
        allow_legacy_fallback
        and mid == DEFAULT_MARKET_ID
        and not configured.exists()
    ):
        root = repo_root if repo_root is not None else _REPO_ROOT
        legacy = root / LEGACY_SQLITE_RELPATH
        if legacy.is_file():
            logger.warning(
                "market %s: configured journal %s missing; using legacy %s "
                "(rename to %s to silence this)",
                mid,
                configured,
                legacy,
                root / market_sqlite_relpath(mid),
            )
            return legacy.resolve()
    return configured


def _pairs_from_market_file(mf: MarketFile) -> PairsConfig | None:
    """Parse inventory as PairsConfig for Bybit/Fluxion-shaped markets."""
    # Only bybit-fluxion inventory is PairsConfig today. Binance uses a
    # different pair leg shape (binance: / pancake:) — loaders land in M7-3.
    if mf.id != DEFAULT_MARKET_ID and mf.cex.venue != "bybit":
        return None
    inv = dict(mf.inventory)
    # Market files nest the former pairs.yaml body under inventory:.
    return PairsConfig.model_validate(inv)


def load_market_context(
    market_id: str = DEFAULT_MARKET_ID,
    *,
    markets_dir: Path | None = None,
    market_path: Path | None = None,
    collector_path: Path | None = None,
    metrics_path: Path | None = None,
    sqlite_path: Path | None = None,
    repo_root: Path | None = None,
    load_collector: bool = True,
) -> MarketContext:
    """Assemble configs for one market. Fail-fast on missing default-market deps."""
    mid = normalize_market_id(market_id)
    root = repo_root if repo_root is not None else _REPO_ROOT
    mf = load_market_file(mid, path=market_path, markets_dir=markets_dir)
    inv_path = (
        market_path
        if market_path is not None
        else market_file_path(mid, markets_dir=markets_dir)
    )

    pairs: PairsConfig | None
    try:
        pairs = _pairs_from_market_file(mf)
    except Exception as exc:  # pydantic ValidationError
        if mid == DEFAULT_MARKET_ID:
            raise MarketConfigError(
                f"default market {mid} inventory is not a valid PairsConfig: {exc}"
            ) from exc
        pairs = None

    base_metrics = load_metrics_config(metrics_path)
    metrics = apply_market_costs(base_metrics, mf.costs)

    collector: CollectorConfig | None = None
    configured_db: Path
    if load_collector:
        try:
            collector = load_collector_config(collector_path, market_id=mid)
            configured_db = (
                sqlite_path
                if sqlite_path is not None
                else collector.resolved_sqlite_path(repo_root=root)
            )
        except Exception as exc:
            if mid == DEFAULT_MARKET_ID:
                raise MarketConfigError(
                    f"collector config for market {mid} failed: {exc}"
                ) from exc
            # Non-default markets may lack a full collector section until M7-3.
            collector = None
            rel = market_sqlite_relpath(mid)
            configured_db = (
                sqlite_path if sqlite_path is not None else (root / rel)
            )
    else:
        rel = market_sqlite_relpath(mid)
        configured_db = sqlite_path if sqlite_path is not None else (root / rel)

    db = resolve_market_sqlite(
        market_id=mid,
        configured=configured_db,
        repo_root=root,
    )

    return MarketContext(
        market_id=mid,
        display_name=mf.display_name,
        cex=mf.cex,
        dex=mf.dex,
        costs=mf.costs,
        market_file=mf,
        pairs=pairs,
        metrics=metrics,
        collector=collector,
        sqlite_path=db,
        inventory_path=inv_path,
    )
