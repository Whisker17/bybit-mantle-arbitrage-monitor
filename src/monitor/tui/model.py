"""Pure view models for the TUI (no Textual dependency).

Overview and detail pages consume these dataclasses. Numbers come from
``monitor.metrics`` and ``monitor.attribution`` builders so the panel cannot
drift from M3/M4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from monitor.attribution.aggregate import PairAttribution
from monitor.metrics.edge import CostBreakdown, Direction, EdgeResult, VenueKind
from monitor.metrics.session import SessionKind
from monitor.metrics.stats import BreachStats, Distribution
from monitor.tui.config import SortKey


@dataclass(frozen=True, slots=True)
class PairOverviewRow:
    """One overview table row (issue WHI-734 primary page)."""

    pair_id: str
    name: str
    low_liquidity: bool
    session: SessionKind | None
    bybit_bid: Decimal | None
    bybit_ask: Decimal | None
    bybit_mid: Decimal | None
    amm_mid: Decimal | None
    rfq_buy: Decimal | None
    rfq_sell: Decimal | None
    amm_spread_bps: Decimal | None
    rfq_spread_bps: Decimal | None  # best absolute of buy/sell side vs Bybit mid
    net_edge_bps: Decimal | None
    net_edge_venue: VenueKind | None
    net_edge_direction: Direction | None
    reference_size_usd: Decimal
    volume_24h: Decimal  # Bybit notional proxy
    trades_24h: int  # Bybit prints + Fluxion swaps
    stale: bool = False  # True when no Bybit book yet


@dataclass(frozen=True, slots=True)
class OverviewModel:
    generated_ts_ms: int
    session_now: SessionKind
    sort_key: SortKey
    sort_desc: bool
    reference_size_usd: Decimal
    rows: list[PairOverviewRow]
    db_path: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SpreadPoint:
    ts_ms: int
    amm_spread_bps: Decimal | None
    session: SessionKind


@dataclass(frozen=True, slots=True)
class TradeStreamRow:
    """One Fluxion fill line for the detail scroll (AMM or RFQ)."""

    ts_ms: int
    mechanism: str  # "amm" | "rfq"
    direction: str
    notional_usd: Decimal | None
    price: Decimal | None
    bybit_mid: Decimal | None
    converging: bool | None  # None = unscored
    taker: str | None
    taker_label: str | None
    tx_hash: str


@dataclass(frozen=True, slots=True)
class EdgePanel:
    """Current edge + cumulative stats + cost breakdown at reference size."""

    current: EdgeResult | None
    distribution_all: Distribution
    distribution_open: Distribution
    distribution_closed: Distribution
    breach_all: BreachStats
    breach_open: BreachStats
    breach_closed: BreachStats
    costs: CostBreakdown | None


@dataclass(frozen=True, slots=True)
class PairDetailModel:
    pair_id: str
    name: str
    low_liquidity: bool
    generated_ts_ms: int
    session_now: SessionKind
    overview: PairOverviewRow
    spread_series: list[SpreadPoint]
    trades: list[TradeStreamRow]
    edge_amm: EdgePanel
    edge_rfq: EdgePanel
    attribution: PairAttribution | None
    # bot vs MM share from attribution labels / mechanism
    arb_bot_trade_share: float | None
    price_keeper_trade_share: float | None
    rfq_mechanism_share: float | None
    error: str | None = None


@dataclass
class SeriesKey:
    pair_id: str
    venue: VenueKind
    direction: Direction

    def as_tuple(self) -> tuple[str, VenueKind, Direction]:
        return (self.pair_id, self.venue, self.direction)


@dataclass
class RunningEdgeState:
    """In-memory EdgeStats accumulation across refreshes (process lifetime)."""

    # keyed by (pair_id, venue, direction)
    stats: dict[tuple[str, VenueKind, Direction], object] = field(default_factory=dict)
    last_sample_ts: dict[tuple[str, VenueKind, Direction], int] = field(
        default_factory=dict
    )
