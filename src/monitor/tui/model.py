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
from monitor.metrics.premium import PremiumSnapshot
from monitor.metrics.session import SessionKind
from monitor.metrics.stats import BreachStats, Distribution, EdgeStats
from monitor.metrics.volume import VolumeCompare
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
    volume_24h: Decimal  # legacy TUI cell: CEX journal notional (price_dm × size)
    trades_24h: int  # legacy: CEX journal prints + DEX swaps
    stale: bool = False  # True when no Bybit book yet
    # WHI-777: CEX REST vs DEX swap 24h (API / Web primary surface).
    cex_volume_24h: Decimal | None = None
    dex_volume_24h: Decimal | None = None
    volume_ratio: Decimal | None = None
    dex_trade_count_24h: int | None = None
    cex_trade_count_24h: int | None = None
    dex_volume_truncated: bool = False
    dex_volume_window_start_ms: int | None = None
    # WHI-779: underlying equity + tokenized premium vs underlying.
    underlying_ticker: str | None = None
    underlying_price: Decimal | None = None
    underlying_currency: str | None = None
    underlying_price_type: str | None = None
    underlying_as_of_ms: int | None = None
    underlying_source: str | None = None
    underlying_empty: str | None = None  # "no_data" | "private"
    premium_bps: Decimal | None = None  # CEX vs underlying (default column)
    cex_premium_bps: Decimal | None = None
    amm_premium_bps: Decimal | None = None
    rfq_premium_bps: Decimal | None = None
    premium_type_label: str | None = None


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
    """One historical quote join for the detail spread chart.

    ``amm_spread_bps`` is (AMM mid − Bybit mid) / Bybit in bps.
    ``rfq_spread_bps`` is the mean of available RFQ buy/sell side spreads
    as-of the book timestamp (stable series; overview column still uses
    larger-absolute). ``bybit_mid`` is de-multiplied L1 mid for optional overlay.
    ``cex_premium_bps`` is CEX equity-eq mid vs underlying (WHI-779 chart
    series); None when no as-of underlying print.
    """

    ts_ms: int
    amm_spread_bps: Decimal | None
    session: SessionKind
    rfq_spread_bps: Decimal | None = None
    bybit_mid: Decimal | None = None
    cex_premium_bps: Decimal | None = None


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
class PremiumPanel:
    """Detail-page current premium + journal-window distribution (WHI-779)."""

    current: PremiumSnapshot
    distribution: Distribution  # CEX premium over spread_series points
    distribution_open: Distribution
    distribution_closed: Distribution


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
    # WHI-777: CEX vs DEX volume compare (detail mini-panel).
    volume_compare: VolumeCompare | None = None
    # WHI-779: underlying premium panel.
    premium: PremiumPanel | None = None
    error: str | None = None


@dataclass
class RunningEdgeState:
    """In-memory EdgeStats accumulation across refreshes (process lifetime)."""

    # keyed by (pair_id, venue, direction)
    stats: dict[tuple[str, VenueKind, Direction], EdgeStats] = field(
        default_factory=dict
    )
    last_sample_ts: dict[tuple[str, VenueKind, Direction], int] = field(
        default_factory=dict
    )
    # Pairs whose journal history has been fully walked into stats once.
    # Overview live ticks may populate ``stats`` without this flag; detail
    # cold-start must still rebuild so cumulative percentiles cover the DB.
    history_rebuilt: set[str] = field(default_factory=set)
