"""PnL v2 cash-flow paper arb engine (WHI-756 / DESIGN §2.6).

Pure functions — no I/O. Inputs are de-multiplied L1 (+ optional depth levels),
``AmmPoolState``, and optional RFQ poll quotes. Outputs are serializable
dataclasses for Web/API consumers; does **not** mutate ``OverviewModel``.

Formulas: ``docs/references/hummingbot-pnl.md`` §4–5.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Literal

from monitor.bybit.depth_math import book_notional_depth, book_vwap_for_base
from monitor.metrics.amm_pool import AmmPoolState
from monitor.metrics.amm_slip import (
    amm_quote_in_for_base_out,
    amm_quote_out_for_base_in,
)
from monitor.metrics.bybit_slip import BPS
from monitor.metrics.config import MetricsConfig, PnlV2Config
from monitor.metrics.edge import Direction, VenueKind, mid_from_bid_ask

DepthSource = Literal["l1", "book"]

# Sentinel for unfillable samples inside the optimal-size search.
_NEG_INF = Decimal("-Infinity")


@dataclass(frozen=True, slots=True)
class PnlCostBreakdownUsd:
    """USD cost lines for one paper trade (reporting; PnL uses cash-flow)."""

    bybit_fee_usd: Decimal
    bybit_slip_usd: Decimal
    fluxion_fee_usd: Decimal
    fluxion_slip_usd: Decimal
    gas_usd: Decimal
    basis_usd: Decimal

    @property
    def total_usd(self) -> Decimal:
        return (
            self.bybit_fee_usd
            + self.bybit_slip_usd
            + self.fluxion_fee_usd
            + self.fluxion_slip_usd
            + self.gas_usd
            + self.basis_usd
        )

    def to_dict(self) -> dict[str, str]:
        return {k: str(v) for k, v in asdict(self).items()}


@dataclass(frozen=True, slots=True)
class PnlResult:
    """Cash-flow PnL at one size × direction × venue."""

    pair_id: str
    venue: VenueKind
    direction: Direction
    size_usd: Decimal
    q_base: Decimal
    bybit_mid: Decimal
    spent_usd: Decimal
    recv_usd: Decimal
    pnl_usd: Decimal
    fillable: bool
    costs: PnlCostBreakdownUsd
    bybit_depth_source: DepthSource
    reason: str | None = None
    # True when pnl_usd meets configured min_profit_* (highlight only).
    meets_min_profit: bool = False

    @property
    def pnl_bps(self) -> Decimal | None:
        if self.size_usd <= 0:
            return None
        return self.pnl_usd / self.size_usd * BPS

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "venue": self.venue,
            "direction": self.direction,
            "size_usd": str(self.size_usd),
            "q_base": str(self.q_base),
            "bybit_mid": str(self.bybit_mid),
            "spent_usd": str(self.spent_usd),
            "recv_usd": str(self.recv_usd),
            "pnl_usd": str(self.pnl_usd),
            "pnl_bps": None if self.pnl_bps is None else str(self.pnl_bps),
            "fillable": self.fillable,
            "reason": self.reason,
            "bybit_depth_source": self.bybit_depth_source,
            "meets_min_profit": self.meets_min_profit,
            "costs": self.costs.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class OptimalSizeResult:
    """Best evaluated AMM size for one direction (sample-best, not continuous)."""

    pair_id: str
    direction: Direction
    q_star_usd: Decimal
    pnl_usd: Decimal
    result: PnlResult
    q_min_usd: Decimal
    q_max_usd: Decimal
    depth_cap_usd: Decimal | None  # None = +∞ (L1)
    amm_cap_usd: Decimal
    samples_evaluated: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "direction": self.direction,
            "q_star_usd": str(self.q_star_usd),
            "pnl_usd": str(self.pnl_usd),
            "q_min_usd": str(self.q_min_usd),
            "q_max_usd": str(self.q_max_usd),
            "depth_cap_usd": None if self.depth_cap_usd is None else str(self.depth_cap_usd),
            "amm_cap_usd": str(self.amm_cap_usd),
            "samples_evaluated": self.samples_evaluated,
            "result": self.result.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PnlBucketTable:
    """Fixed AMM bucket rows + optional RFQ poll rows for one snapshot."""

    pair_id: str
    direction: Direction
    amm_buckets: list[PnlResult]
    rfq_rows: list[PnlResult]
    optimal: OptimalSizeResult | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "direction": self.direction,
            "amm_buckets": [r.to_dict() for r in self.amm_buckets],
            "rfq_rows": [r.to_dict() for r in self.rfq_rows],
            "optimal": None if self.optimal is None else self.optimal.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RfqPollQuote:
    """One RFQ poll response consumed by the engine (poll-native sizing)."""

    # EXACT_INPUT amount on Fluxion (USDC for buy base; native base for sell base).
    amount_in: Decimal
    # Counter-asset amountOut from the poll.
    amount_out: Decimal
    # Which Fluxion leg this poll is for (buy base vs sell base).
    fluxion_leg: Literal["buy", "sell"]


def _fee_fraction(config: MetricsConfig) -> Decimal:
    return config.bybit_taker_fee_bps / BPS


def _basis_usd(config: MetricsConfig, size_usd: Decimal) -> Decimal:
    return config.usdt_usdc_basis_bps / BPS * size_usd


def _zero_costs(*, gas: Decimal = Decimal(0), basis: Decimal = Decimal(0)) -> PnlCostBreakdownUsd:
    return PnlCostBreakdownUsd(
        bybit_fee_usd=Decimal(0),
        bybit_slip_usd=Decimal(0),
        fluxion_fee_usd=Decimal(0),
        fluxion_slip_usd=Decimal(0),
        gas_usd=gas,
        basis_usd=basis,
    )


def _meets_min_profit(
    pnl: PnlV2Config,
    *,
    pnl_usd: Decimal,
    size_usd: Decimal,
    fillable: bool,
) -> bool:
    if not fillable:
        return False
    ok = True
    if pnl.min_profit_usd is not None:
        ok = ok and pnl_usd >= pnl.min_profit_usd
    if pnl.min_profit_bps is not None and size_usd > 0:
        ok = ok and (pnl_usd / size_usd * BPS) >= pnl.min_profit_bps
    if pnl.min_profit_usd is None and pnl.min_profit_bps is None:
        return pnl_usd > 0
    return ok


def _bybit_vwap(
    *,
    bid: Decimal,
    ask: Decimal,
    base_qty: Decimal,
    side: Literal["bid", "ask"],
    depth: list[tuple[Decimal, Decimal]] | None,
) -> tuple[Decimal, DepthSource] | None:
    """Return (vwap, source) for ``base_qty`` on the traded side, or None.

    Empty depth list degrades to L1 (same as missing depth) — hummingbot-pnl §5.3.
    """
    if base_qty <= 0:
        return None
    if depth:  # non-empty multi-level book only
        vwap = book_vwap_for_base(depth, base_qty)
        if vwap is None:
            return None
        return vwap, "book"
    # L1: infinite size at top of book.
    px = bid if side == "bid" else ask
    if px <= 0:
        return None
    return px, "l1"


@dataclass(frozen=True, slots=True)
class _BybitLegCash:
    """Bybit cash after received-asset fee + slip vs mid (USD)."""

    cash_usd: Decimal  # recv for sell, spent for buy
    fee_usd: Decimal
    slip_usd: Decimal
    depth_source: DepthSource


def _bybit_sell_cash(
    *,
    bid: Decimal,
    ask: Decimal,
    bybit_mid: Decimal,
    q: Decimal,
    f_b: Decimal,
    depth: list[tuple[Decimal, Decimal]] | None,
) -> _BybitLegCash | None:
    """Sell net base ``q`` into bids; returns USDT received after fee."""
    vwap_res = _bybit_vwap(
        bid=bid, ask=ask, base_qty=q, side="bid", depth=depth
    )
    if vwap_res is None:
        return None
    p_bid, depth_src = vwap_res
    fee = q * p_bid * f_b
    recv = q * p_bid * (1 - f_b)
    slip = q * (bybit_mid - p_bid) if bybit_mid > p_bid else Decimal(0)
    return _BybitLegCash(
        cash_usd=recv, fee_usd=fee, slip_usd=slip, depth_source=depth_src
    )


def _bybit_buy_cash(
    *,
    bid: Decimal,
    ask: Decimal,
    bybit_mid: Decimal,
    q: Decimal,
    f_b: Decimal,
    depth: list[tuple[Decimal, Decimal]] | None,
) -> _BybitLegCash | None:
    """Buy so **net** base is ``q`` after fee-in-base; returns USDT spent."""
    if f_b >= 1:
        return None
    q_gross = q / (1 - f_b)
    vwap_res = _bybit_vwap(
        bid=bid, ask=ask, base_qty=q_gross, side="ask", depth=depth
    )
    if vwap_res is None:
        return None
    p_ask, depth_src = vwap_res
    spent = q_gross * p_ask
    fee = spent - q * p_ask
    slip = q_gross * (p_ask - bybit_mid) if p_ask > bybit_mid else Decimal(0)
    return _BybitLegCash(
        cash_usd=spent, fee_usd=fee, slip_usd=slip, depth_source=depth_src
    )


def _amm_buy_cash(
    amm: AmmPoolState,
    q: Decimal,
    pnl_cfg: PnlV2Config,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Buy net base ``q`` on AMM. Returns (usdc_spent, fee_usd, slip_usd) or None."""
    mid = amm.mid_quote_per_base()
    usdc = amm_quote_in_for_base_out(
        amm,
        q,
        q_tol_rel=pnl_cfg.q_tol_rel,
        max_iters=pnl_cfg.amm_solve_max_iters,
        apply_pool_fee=True,
    )
    if usdc is None:
        return None
    usdc_no_fee = amm_quote_in_for_base_out(
        amm,
        q,
        q_tol_rel=pnl_cfg.q_tol_rel,
        max_iters=pnl_cfg.amm_solve_max_iters,
        apply_pool_fee=False,
    )
    mid_cost = q * mid
    if usdc_no_fee is None:
        # Fall back: attribute all excess over mid to combined fee+slip.
        fee = Decimal(0)
        slip = usdc - mid_cost if usdc > mid_cost else Decimal(0)
    else:
        fee = usdc - usdc_no_fee if usdc > usdc_no_fee else Decimal(0)
        slip = usdc_no_fee - mid_cost if usdc_no_fee > mid_cost else Decimal(0)
    return usdc, fee, slip


def _amm_sell_cash(
    amm: AmmPoolState,
    q: Decimal,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Sell base ``q`` on AMM. Returns (usdc_recv, fee_usd, slip_usd) or None."""
    mid = amm.mid_quote_per_base()
    usdc = amm_quote_out_for_base_in(amm, q, apply_pool_fee=True)
    if usdc is None:
        return None
    usdc_no_fee = amm_quote_out_for_base_in(amm, q, apply_pool_fee=False)
    mid_recv = q * mid
    if usdc_no_fee is None:
        fee = Decimal(0)
        slip = mid_recv - usdc if mid_recv > usdc else Decimal(0)
    else:
        fee = usdc_no_fee - usdc if usdc_no_fee > usdc else Decimal(0)
        slip = mid_recv - usdc_no_fee if mid_recv > usdc_no_fee else Decimal(0)
    return usdc, fee, slip


def compute_pnl_usd(
    *,
    pair_id: str,
    bybit_bid: Decimal,
    bybit_ask: Decimal,
    size_usd: Decimal,
    direction: Direction,
    venue: VenueKind,
    config: MetricsConfig,
    amm: AmmPoolState | None = None,
    bybit_bids: list[tuple[Decimal, Decimal]] | None = None,
    bybit_asks: list[tuple[Decimal, Decimal]] | None = None,
    rfq: RfqPollQuote | None = None,
) -> PnlResult:
    """Cash-flow PnL for one paper arb at ``size_usd`` (AMM) or RFQ poll size.

    Depth lists must be de-multiplied ``(price, size)`` ordered best-first.
    Without depth, L1 bid/ask are used (infinite size at top).
    """
    pnl_cfg = config.pnl_v2
    if bybit_bid <= 0 or bybit_ask <= 0:
        return _unfillable_result(
            pair_id=pair_id,
            venue=venue,
            direction=direction,
            size_usd=size_usd,
            q_base=Decimal(0),
            bybit_mid=Decimal(0),
            reason="missing_quote",
            depth_source="l1",
            config=config,
        )
    bybit_mid = mid_from_bid_ask(bybit_bid, bybit_ask)
    gas = config.gas_usd_per_swap
    if venue == "rfq" and not pnl_cfg.gas_on_rfq:
        gas = Decimal(0)

    if venue == "rfq":
        return _pnl_rfq(
            pair_id=pair_id,
            bybit_bid=bybit_bid,
            bybit_ask=bybit_ask,
            bybit_mid=bybit_mid,
            direction=direction,
            config=config,
            pnl_cfg=pnl_cfg,
            gas=gas,
            bybit_bids=bybit_bids,
            bybit_asks=bybit_asks,
            rfq=rfq,
        )

    if size_usd <= 0:
        raise ValueError("size_usd must be positive for AMM venue")
    if amm is None:
        raise ValueError("amm venue requires AmmPoolState")

    q = size_usd / bybit_mid
    f_b = _fee_fraction(config)
    basis = _basis_usd(config, size_usd)

    if direction == "buy_fluxion_sell_bybit":
        return _pnl_buy_fluxion_sell_bybit(
            pair_id=pair_id,
            bybit_bid=bybit_bid,
            bybit_ask=bybit_ask,
            bybit_mid=bybit_mid,
            size_usd=size_usd,
            q=q,
            f_b=f_b,
            basis=basis,
            gas=gas,
            config=config,
            pnl_cfg=pnl_cfg,
            amm=amm,
            bybit_bids=bybit_bids,
        )
    return _pnl_buy_bybit_sell_fluxion(
        pair_id=pair_id,
        bybit_bid=bybit_bid,
        bybit_ask=bybit_ask,
        bybit_mid=bybit_mid,
        size_usd=size_usd,
        q=q,
        f_b=f_b,
        basis=basis,
        gas=gas,
        config=config,
        pnl_cfg=pnl_cfg,
        amm=amm,
        bybit_asks=bybit_asks,
    )


def _unfillable_result(
    *,
    pair_id: str,
    venue: VenueKind,
    direction: Direction,
    size_usd: Decimal,
    q_base: Decimal,
    bybit_mid: Decimal,
    reason: str,
    depth_source: DepthSource,
    config: MetricsConfig,
    gas: Decimal | None = None,
) -> PnlResult:
    g = config.gas_usd_per_swap if gas is None else gas
    basis = _basis_usd(config, size_usd) if size_usd > 0 else Decimal(0)
    costs = _zero_costs(gas=g, basis=basis)
    return PnlResult(
        pair_id=pair_id,
        venue=venue,
        direction=direction,
        size_usd=size_usd,
        q_base=q_base,
        bybit_mid=bybit_mid,
        spent_usd=Decimal(0),
        recv_usd=Decimal(0),
        pnl_usd=Decimal(0),
        fillable=False,
        costs=costs,
        bybit_depth_source=depth_source,
        reason=reason,
        meets_min_profit=False,
    )


def _pnl_buy_fluxion_sell_bybit(
    *,
    pair_id: str,
    bybit_bid: Decimal,
    bybit_ask: Decimal,
    bybit_mid: Decimal,
    size_usd: Decimal,
    q: Decimal,
    f_b: Decimal,
    basis: Decimal,
    gas: Decimal,
    config: MetricsConfig,
    pnl_cfg: PnlV2Config,
    amm: AmmPoolState,
    bybit_bids: list[tuple[Decimal, Decimal]] | None,
) -> PnlResult:
    amm_cash = _amm_buy_cash(amm, q, pnl_cfg)
    if amm_cash is None:
        return _unfillable_result(
            pair_id=pair_id,
            venue="amm",
            direction="buy_fluxion_sell_bybit",
            size_usd=size_usd,
            q_base=q,
            bybit_mid=bybit_mid,
            reason="unfillable_or_range_exhausted",
            depth_source="book" if bybit_bids else "l1",
            config=config,
            gas=gas,
        )
    usdc_spent, flux_fee, flux_slip = amm_cash

    bybit = _bybit_sell_cash(
        bid=bybit_bid,
        ask=bybit_ask,
        bybit_mid=bybit_mid,
        q=q,
        f_b=f_b,
        depth=bybit_bids,
    )
    if bybit is None:
        return _unfillable_result(
            pair_id=pair_id,
            venue="amm",
            direction="buy_fluxion_sell_bybit",
            size_usd=size_usd,
            q_base=q,
            bybit_mid=bybit_mid,
            reason="bybit_book_unfillable",
            depth_source="book" if bybit_bids else "l1",
            config=config,
            gas=gas,
        )

    pnl_usd = bybit.cash_usd - usdc_spent - gas - basis
    costs = PnlCostBreakdownUsd(
        bybit_fee_usd=bybit.fee_usd,
        bybit_slip_usd=bybit.slip_usd,
        fluxion_fee_usd=flux_fee,
        fluxion_slip_usd=flux_slip,
        gas_usd=gas,
        basis_usd=basis,
    )
    return PnlResult(
        pair_id=pair_id,
        venue="amm",
        direction="buy_fluxion_sell_bybit",
        size_usd=size_usd,
        q_base=q,
        bybit_mid=bybit_mid,
        spent_usd=usdc_spent,
        recv_usd=bybit.cash_usd,
        pnl_usd=pnl_usd,
        fillable=True,
        costs=costs,
        bybit_depth_source=bybit.depth_source,
        reason=None,
        meets_min_profit=_meets_min_profit(
            pnl_cfg, pnl_usd=pnl_usd, size_usd=size_usd, fillable=True
        ),
    )


def _pnl_buy_bybit_sell_fluxion(
    *,
    pair_id: str,
    bybit_bid: Decimal,
    bybit_ask: Decimal,
    bybit_mid: Decimal,
    size_usd: Decimal,
    q: Decimal,
    f_b: Decimal,
    basis: Decimal,
    gas: Decimal,
    config: MetricsConfig,
    pnl_cfg: PnlV2Config,
    amm: AmmPoolState,
    bybit_asks: list[tuple[Decimal, Decimal]] | None,
) -> PnlResult:
    bybit = _bybit_buy_cash(
        bid=bybit_bid,
        ask=bybit_ask,
        bybit_mid=bybit_mid,
        q=q,
        f_b=f_b,
        depth=bybit_asks,
    )
    if bybit is None:
        reason = "invalid_fee" if f_b >= 1 else "bybit_book_unfillable"
        return _unfillable_result(
            pair_id=pair_id,
            venue="amm",
            direction="buy_bybit_sell_fluxion",
            size_usd=size_usd,
            q_base=q,
            bybit_mid=bybit_mid,
            reason=reason,
            depth_source="book" if bybit_asks else "l1",
            config=config,
            gas=gas,
        )

    amm_cash = _amm_sell_cash(amm, q)
    if amm_cash is None:
        return _unfillable_result(
            pair_id=pair_id,
            venue="amm",
            direction="buy_bybit_sell_fluxion",
            size_usd=size_usd,
            q_base=q,
            bybit_mid=bybit_mid,
            reason="unfillable_or_range_exhausted",
            depth_source=bybit.depth_source,
            config=config,
            gas=gas,
        )
    usdc_recv, flux_fee, flux_slip = amm_cash
    pnl_usd = usdc_recv - bybit.cash_usd - gas - basis
    costs = PnlCostBreakdownUsd(
        bybit_fee_usd=bybit.fee_usd,
        bybit_slip_usd=bybit.slip_usd,
        fluxion_fee_usd=flux_fee,
        fluxion_slip_usd=flux_slip,
        gas_usd=gas,
        basis_usd=basis,
    )
    return PnlResult(
        pair_id=pair_id,
        venue="amm",
        direction="buy_bybit_sell_fluxion",
        size_usd=size_usd,
        q_base=q,
        bybit_mid=bybit_mid,
        spent_usd=bybit.cash_usd,
        recv_usd=usdc_recv,
        pnl_usd=pnl_usd,
        fillable=True,
        costs=costs,
        bybit_depth_source=bybit.depth_source,
        reason=None,
        meets_min_profit=_meets_min_profit(
            pnl_cfg, pnl_usd=pnl_usd, size_usd=size_usd, fillable=True
        ),
    )


def _pnl_rfq(
    *,
    pair_id: str,
    bybit_bid: Decimal,
    bybit_ask: Decimal,
    bybit_mid: Decimal,
    direction: Direction,
    config: MetricsConfig,
    pnl_cfg: PnlV2Config,
    gas: Decimal,
    bybit_bids: list[tuple[Decimal, Decimal]] | None,
    bybit_asks: list[tuple[Decimal, Decimal]] | None,
    rfq: RfqPollQuote | None,
) -> PnlResult:
    if rfq is None:
        return _unfillable_result(
            pair_id=pair_id,
            venue="rfq",
            direction=direction,
            size_usd=Decimal(0),
            q_base=Decimal(0),
            bybit_mid=bybit_mid,
            reason="missing_quote",
            depth_source="l1",
            config=config,
            gas=gas,
        )
    f_b = _fee_fraction(config)

    if direction == "buy_fluxion_sell_bybit":
        if rfq.fluxion_leg != "buy":
            return _unfillable_result(
                pair_id=pair_id,
                venue="rfq",
                direction=direction,
                size_usd=Decimal(0),
                q_base=Decimal(0),
                bybit_mid=bybit_mid,
                reason="rfq_leg_mismatch",
                depth_source="l1",
                config=config,
                gas=gas,
            )
        usdc_spent = rfq.amount_in
        q = rfq.amount_out
        size_usd = q * bybit_mid
        basis = _basis_usd(config, size_usd)
        bybit = _bybit_sell_cash(
            bid=bybit_bid,
            ask=bybit_ask,
            bybit_mid=bybit_mid,
            q=q,
            f_b=f_b,
            depth=bybit_bids,
        )
        if bybit is None:
            return _unfillable_result(
                pair_id=pair_id,
                venue="rfq",
                direction=direction,
                size_usd=size_usd,
                q_base=q,
                bybit_mid=bybit_mid,
                reason="bybit_book_unfillable",
                depth_source="book" if bybit_bids else "l1",
                config=config,
                gas=gas,
            )
        pnl_usd = bybit.cash_usd - usdc_spent - gas - basis
        costs = PnlCostBreakdownUsd(
            bybit_fee_usd=bybit.fee_usd,
            bybit_slip_usd=bybit.slip_usd,
            fluxion_fee_usd=Decimal(0),
            fluxion_slip_usd=Decimal(0),
            gas_usd=gas,
            basis_usd=basis,
        )
        return PnlResult(
            pair_id=pair_id,
            venue="rfq",
            direction=direction,
            size_usd=size_usd,
            q_base=q,
            bybit_mid=bybit_mid,
            spent_usd=usdc_spent,
            recv_usd=bybit.cash_usd,
            pnl_usd=pnl_usd,
            fillable=True,
            costs=costs,
            bybit_depth_source=bybit.depth_source,
            meets_min_profit=_meets_min_profit(
                pnl_cfg, pnl_usd=pnl_usd, size_usd=size_usd, fillable=True
            ),
        )

    # buy_bybit_sell_fluxion
    if rfq.fluxion_leg != "sell":
        return _unfillable_result(
            pair_id=pair_id,
            venue="rfq",
            direction=direction,
            size_usd=Decimal(0),
            q_base=Decimal(0),
            bybit_mid=bybit_mid,
            reason="rfq_leg_mismatch",
            depth_source="l1",
            config=config,
            gas=gas,
        )
    q = rfq.amount_in
    usdc_recv = rfq.amount_out
    size_usd = q * bybit_mid
    basis = _basis_usd(config, size_usd)
    bybit = _bybit_buy_cash(
        bid=bybit_bid,
        ask=bybit_ask,
        bybit_mid=bybit_mid,
        q=q,
        f_b=f_b,
        depth=bybit_asks,
    )
    if bybit is None:
        reason = "invalid_fee" if f_b >= 1 else "bybit_book_unfillable"
        return _unfillable_result(
            pair_id=pair_id,
            venue="rfq",
            direction=direction,
            size_usd=size_usd,
            q_base=q,
            bybit_mid=bybit_mid,
            reason=reason,
            depth_source="book" if bybit_asks else "l1",
            config=config,
            gas=gas,
        )
    pnl_usd = usdc_recv - bybit.cash_usd - gas - basis
    costs = PnlCostBreakdownUsd(
        bybit_fee_usd=bybit.fee_usd,
        bybit_slip_usd=bybit.slip_usd,
        fluxion_fee_usd=Decimal(0),
        fluxion_slip_usd=Decimal(0),
        gas_usd=gas,
        basis_usd=basis,
    )
    return PnlResult(
        pair_id=pair_id,
        venue="rfq",
        direction=direction,
        size_usd=size_usd,
        q_base=q,
        bybit_mid=bybit_mid,
        spent_usd=bybit.cash_usd,
        recv_usd=usdc_recv,
        pnl_usd=pnl_usd,
        fillable=True,
        costs=costs,
        bybit_depth_source=bybit.depth_source,
        meets_min_profit=_meets_min_profit(
            pnl_cfg, pnl_usd=pnl_usd, size_usd=size_usd, fillable=True
        ),
    )


def pnl_bucket_table(
    *,
    pair_id: str,
    bybit_bid: Decimal,
    bybit_ask: Decimal,
    direction: Direction,
    config: MetricsConfig,
    amm: AmmPoolState | None = None,
    bybit_bids: list[tuple[Decimal, Decimal]] | None = None,
    bybit_asks: list[tuple[Decimal, Decimal]] | None = None,
    rfq_quotes: list[RfqPollQuote] | None = None,
    include_optimal: bool = True,
) -> PnlBucketTable:
    """Fixed AMM bucket PnL rows (+ optional RFQ poll rows and optimal size)."""
    pnl_cfg = config.pnl_v2
    if amm is None:
        raise ValueError("pnl_bucket_table requires AmmPoolState for AMM buckets")

    amm_buckets = [
        compute_pnl_usd(
            pair_id=pair_id,
            bybit_bid=bybit_bid,
            bybit_ask=bybit_ask,
            size_usd=size,
            direction=direction,
            venue="amm",
            config=config,
            amm=amm,
            bybit_bids=bybit_bids,
            bybit_asks=bybit_asks,
        )
        for size in pnl_cfg.buckets_usd
    ]

    rfq_rows: list[PnlResult] = []
    for quote in rfq_quotes or []:
        # Map poll leg → direction automatically when it matches.
        if quote.fluxion_leg == "buy" and direction != "buy_fluxion_sell_bybit":
            continue
        if quote.fluxion_leg == "sell" and direction != "buy_bybit_sell_fluxion":
            continue
        rfq_rows.append(
            compute_pnl_usd(
                pair_id=pair_id,
                bybit_bid=bybit_bid,
                bybit_ask=bybit_ask,
                size_usd=Decimal(0),  # ignored for RFQ
                direction=direction,
                venue="rfq",
                config=config,
                bybit_bids=bybit_bids,
                bybit_asks=bybit_asks,
                rfq=quote,
            )
        )

    optimal = None
    if include_optimal:
        optimal = optimal_size(
            pair_id=pair_id,
            bybit_bid=bybit_bid,
            bybit_ask=bybit_ask,
            direction=direction,
            config=config,
            amm=amm,
            bybit_bids=bybit_bids,
            bybit_asks=bybit_asks,
        )

    return PnlBucketTable(
        pair_id=pair_id,
        direction=direction,
        amm_buckets=amm_buckets,
        rfq_rows=rfq_rows,
        optimal=optimal,
    )


def _depth_cap_usd(
    *,
    direction: Direction,
    bybit_bids: list[tuple[Decimal, Decimal]] | None,
    bybit_asks: list[tuple[Decimal, Decimal]] | None,
    pnl_cfg: PnlV2Config,
) -> Decimal | None:
    """USD notional cap from book depth. None = +∞."""
    if direction == "buy_fluxion_sell_bybit":
        levels = bybit_bids
    else:
        levels = bybit_asks
    # Empty list ≡ missing depth (L1 / +∞), per hummingbot-pnl §5.3.
    if levels:
        return book_notional_depth(levels)
    return pnl_cfg.l1_assumed_size_usd


def _amm_leg_fillable(
    *,
    size_usd: Decimal,
    bybit_mid: Decimal,
    direction: Direction,
    amm: AmmPoolState,
    pnl_cfg: PnlV2Config,
) -> bool:
    q = size_usd / bybit_mid
    if direction == "buy_fluxion_sell_bybit":
        return (
            amm_quote_in_for_base_out(
                amm,
                q,
                q_tol_rel=pnl_cfg.q_tol_rel,
                max_iters=pnl_cfg.amm_solve_max_iters,
            )
            is not None
        )
    return amm_quote_out_for_base_in(amm, q) is not None


def _probe_amm_cap(
    *,
    q_min: Decimal,
    config_cap: Decimal,
    bybit_mid: Decimal,
    direction: Direction,
    amm: AmmPoolState,
    pnl_cfg: PnlV2Config,
) -> Decimal:
    """Geometric ramp + log binary search for last fillable Q (hummingbot-pnl §5.3)."""
    q = q_min
    last_ok = q_min if _amm_leg_fillable(
        size_usd=q_min,
        bybit_mid=bybit_mid,
        direction=direction,
        amm=amm,
        pnl_cfg=pnl_cfg,
    ) else None

    q_hi_fail: Decimal | None = None
    while q < config_cap:
        nxt = min(q * 2, config_cap)
        if nxt == q:
            break
        q = nxt
        ok = _amm_leg_fillable(
            size_usd=q,
            bybit_mid=bybit_mid,
            direction=direction,
            amm=amm,
            pnl_cfg=pnl_cfg,
        )
        if ok:
            last_ok = q
        else:
            q_hi_fail = q
            break

    if last_ok is None:
        return Decimal(0)
    if q_hi_fail is None:
        return config_cap

    # Binary search last fillable between last_ok and q_hi_fail (log space).
    lo = last_ok
    hi = q_hi_fail
    for _ in range(pnl_cfg.amm_cap_max_iters):
        if lo <= 0 or hi <= 0:
            break
        log_mid = (math.log(float(lo)) + math.log(float(hi))) / 2
        mid_q = Decimal(str(math.exp(log_mid)))
        if mid_q <= lo or mid_q >= hi:
            break
        if _amm_leg_fillable(
            size_usd=mid_q,
            bybit_mid=bybit_mid,
            direction=direction,
            amm=amm,
            pnl_cfg=pnl_cfg,
        ):
            lo = mid_q
        else:
            hi = mid_q
    return lo


def optimal_size(
    *,
    pair_id: str,
    bybit_bid: Decimal,
    bybit_ask: Decimal,
    direction: Direction,
    config: MetricsConfig,
    amm: AmmPoolState,
    bybit_bids: list[tuple[Decimal, Decimal]] | None = None,
    bybit_asks: list[tuple[Decimal, Decimal]] | None = None,
) -> OptimalSizeResult | None:
    """Sample-best Q* maximizing PnL (log grid + peak refine + endpoints).

    Does **not** assume concavity. Claim: best among evaluated samples only.
    """
    pnl_cfg = config.pnl_v2
    if bybit_bid <= 0 or bybit_ask <= 0:
        return None
    bybit_mid = mid_from_bid_ask(bybit_bid, bybit_ask)
    q_min = pnl_cfg.q_min_usd
    config_cap = pnl_cfg.config_cap_usd

    depth_cap = _depth_cap_usd(
        direction=direction,
        bybit_bids=bybit_bids,
        bybit_asks=bybit_asks,
        pnl_cfg=pnl_cfg,
    )
    amm_cap = _probe_amm_cap(
        q_min=q_min,
        config_cap=config_cap,
        bybit_mid=bybit_mid,
        direction=direction,
        amm=amm,
        pnl_cfg=pnl_cfg,
    )
    caps = [config_cap, amm_cap]
    if depth_cap is not None:
        caps.append(depth_cap)
    q_max = min(caps)
    if q_max < q_min:
        return None

    def _eval(size: Decimal) -> PnlResult:
        return compute_pnl_usd(
            pair_id=pair_id,
            bybit_bid=bybit_bid,
            bybit_ask=bybit_ask,
            size_usd=size,
            direction=direction,
            venue="amm",
            config=config,
            amm=amm,
            bybit_bids=bybit_bids,
            bybit_asks=bybit_asks,
        )

    # 1. Coarse log grid
    n = pnl_cfg.coarse_points
    log_min = math.log(float(q_min))
    log_max = math.log(float(q_max))
    coarse_q: list[Decimal] = []
    for i in range(n):
        if n == 1:
            q = q_min
        else:
            q = Decimal(str(math.exp(log_min + (log_max - log_min) * i / (n - 1))))
        coarse_q.append(q)
    # Force exact endpoints (float exp may drift).
    coarse_q[0] = q_min
    coarse_q[-1] = q_max

    coarse_results: list[PnlResult] = [_eval(q) for q in coarse_q]
    samples = list(coarse_results)

    def _score(r: PnlResult) -> Decimal:
        return r.pnl_usd if r.fillable else _NEG_INF

    scores = [_score(r) for r in coarse_results]

    # 2. Local peaks (finite PnL only)
    peak_idxs: list[int] = []
    for i, sc in enumerate(scores):
        if sc == _NEG_INF:
            continue
        left = scores[i - 1] if i > 0 else _NEG_INF
        right = scores[i + 1] if i + 1 < len(scores) else _NEG_INF
        if sc >= left and sc >= right:
            peak_idxs.append(i)

    # Plateau guard: global coarse argmax among fillable
    finite = [(i, sc) for i, sc in enumerate(scores) if sc != _NEG_INF]
    if finite:
        best_i = max(finite, key=lambda t: (t[1], -float(coarse_q[t[0]])))[0]
        if best_i not in peak_idxs:
            peak_idxs.append(best_i)

    # 3. Refine each peak between nearest fillable coarse neighbors
    for i in peak_idxs:
        left_q = q_min
        for j in range(i - 1, -1, -1):
            if scores[j] != _NEG_INF:
                left_q = coarse_q[j]
                break
        right_q = q_max
        for j in range(i + 1, len(scores)):
            if scores[j] != _NEG_INF:
                right_q = coarse_q[j]
                break
        if right_q <= left_q:
            continue
        m = pnl_cfg.refine_points
        for k in range(m):
            if m == 1:
                rq = left_q
            else:
                rq = left_q + (right_q - left_q) * Decimal(k) / Decimal(m - 1)
            samples.append(_eval(rq))

    # 4. Endpoint check — skip if coarse grid already evaluated them.
    seen_sizes = {r.size_usd for r in samples}
    if q_min not in seen_sizes:
        samples.append(_eval(q_min))
    if q_max not in seen_sizes:
        samples.append(_eval(q_max))

    fillable_samples = [r for r in samples if r.fillable]
    if not fillable_samples:
        return None

    # 5. Winner: max PnL; ties → smaller Q
    winner = max(fillable_samples, key=lambda r: (r.pnl_usd, -r.size_usd))
    return OptimalSizeResult(
        pair_id=pair_id,
        direction=direction,
        q_star_usd=winner.size_usd,
        pnl_usd=winner.pnl_usd,
        result=winner,
        q_min_usd=q_min,
        q_max_usd=q_max,
        depth_cap_usd=depth_cap,
        amm_cap_usd=amm_cap,
        samples_evaluated=len(samples),
    )


__all__ = [
    "DepthSource",
    "OptimalSizeResult",
    "PnlBucketTable",
    "PnlCostBreakdownUsd",
    "PnlResult",
    "RfqPollQuote",
    "compute_pnl_usd",
    "optimal_size",
    "pnl_bucket_table",
]
