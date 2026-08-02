"""MarketContext — assembled runtime view for one market (WHI-771)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from monitor.attribution.config import AttributionConfig, load_attribution_config
from monitor.collector.config import (
    CollectorConfig,
    CollectorConfigError,
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
from monitor.symbols.bstocks_models import BStocksPairsConfig
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
      inventory shape; ``None`` for binance-pancake.
    * ``bstocks`` — Binance⇄Pancake ``BStocksPairsConfig`` for binance-pancake;
      ``None`` for bybit-fluxion.
    * ``metrics`` — base metrics.yaml with **venue costs overridden** from the
      market file (fee / gas / quote basis).
    * ``attribution`` — attribution thresholds (currently shared YAML; loaded
      here so consumers go through market assembly, not ad-hoc loaders).
    * ``collector`` — market-scoped collector section (both markets after M7-3).
    * ``sqlite_path`` — resolved absolute path to this market's journal.
    """

    market_id: str
    display_name: str
    cex: CexSide
    dex: DexSide
    costs: MarketCosts
    market_file: MarketFile
    pairs: PairsConfig | None
    bstocks: BStocksPairsConfig | None
    metrics: MetricsConfig
    attribution: AttributionConfig
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

    Prefer the configured path. For the default market only, when the
    configured path is the **convention** path (``data/monitor-bybit-fluxion.db``)
    and is missing but legacy ``data/monitor.db`` exists, fall back so a VPS
    journal is not orphaned mid-migration.

    Explicit ``--sqlite`` overrides never fall back (pass
    ``allow_legacy_fallback=False``).
    """
    mid = normalize_market_id(market_id)
    if configured.is_file():
        return configured
    if not allow_legacy_fallback or mid != DEFAULT_MARKET_ID or configured.exists():
        return configured

    root = repo_root if repo_root is not None else _REPO_ROOT
    rel = market_sqlite_relpath(mid)
    convention = (root / rel).resolve()
    try:
        configured_resolved = configured.resolve()
    except OSError:
        configured_resolved = configured
    # Only fall back when the missing path *is* the convention path — never for
    # an operator-supplied custom --sqlite / --db.
    is_convention = (
        configured_resolved == convention
        or configured == Path(rel)
        or configured.as_posix() == rel
    )
    if not is_convention:
        return configured

    legacy = root / LEGACY_SQLITE_RELPATH
    if legacy.is_file():
        logger.warning(
            "market %s: configured journal %s missing; using legacy %s "
            "(rename to %s to silence this)",
            mid,
            configured,
            legacy,
            root / rel,
        )
        return legacy.resolve()
    return configured


def _pairs_from_market_file(mf: MarketFile) -> PairsConfig | None:
    """Parse inventory as PairsConfig for Bybit/Fluxion-shaped markets.

    Non-Bybit markets intentionally return ``None`` (different pair leg shape);
    a Bybit-shaped inventory that fails validation always raises.
    """
    if mf.cex.venue != "bybit":
        return None
    inv = dict(mf.inventory)
    try:
        return PairsConfig.model_validate(inv)
    except ValidationError as exc:
        raise MarketConfigError(
            f"market {mf.id} inventory is not a valid PairsConfig: {exc}"
        ) from exc


def _bstocks_from_market_file(mf: MarketFile) -> BStocksPairsConfig | None:
    """Parse inventory as BStocksPairsConfig for Binance/Pancake markets."""
    if mf.cex.venue != "binance":
        return None
    inv = dict(mf.inventory)
    try:
        return BStocksPairsConfig.model_validate(inv)
    except ValidationError as exc:
        raise MarketConfigError(
            f"market {mf.id} inventory is not a valid BStocksPairsConfig: {exc}"
        ) from exc


def load_market_context(
    market_id: str = DEFAULT_MARKET_ID,
    *,
    markets_dir: Path | None = None,
    market_path: Path | None = None,
    collector_path: Path | None = None,
    metrics_path: Path | None = None,
    attribution_path: Path | None = None,
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

    pairs = _pairs_from_market_file(mf)
    bstocks = _bstocks_from_market_file(mf)

    base_metrics = load_metrics_config(metrics_path)
    metrics = apply_market_costs(base_metrics, mf.costs)
    attribution = load_attribution_config(attribution_path)

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
        except CollectorConfigError as exc:
            if mid == DEFAULT_MARKET_ID:
                raise MarketConfigError(
                    f"collector config for market {mid} failed: {exc}"
                ) from exc
            # Unknown / incomplete venue sections still fail soft for non-default
            # markets so API/Web can open a journal-only context.
            logger.warning("collector config for market %s unavailable: %s", mid, exc)
            collector = None
            rel = market_sqlite_relpath(mid)
            configured_db = (
                sqlite_path if sqlite_path is not None else (root / rel)
            )
    else:
        rel = market_sqlite_relpath(mid)
        configured_db = sqlite_path if sqlite_path is not None else (root / rel)

    # Legacy fallback is gated inside resolve_market_sqlite to the *convention*
    # path only, so config-derived paths (api/tui yaml) still migrate while a
    # custom --sqlite /tmp/foo.db never redirects to data/monitor.db.
    db = resolve_market_sqlite(
        market_id=mid,
        configured=configured_db,
        repo_root=root,
        allow_legacy_fallback=True,
    )

    return MarketContext(
        market_id=mid,
        display_name=mf.display_name,
        cex=mf.cex,
        dex=mf.dex,
        costs=mf.costs,
        market_file=mf,
        pairs=pairs,
        bstocks=bstocks,
        metrics=metrics,
        attribution=attribution,
        collector=collector,
        sqlite_path=db,
        inventory_path=inv_path,
    )
