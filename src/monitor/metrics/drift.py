"""Sequential-execution drift bar (WHI-962).

Live panel prices both legs off one snapshot. Real arb is sequential (buy →
transfer ~10 min → sell). The bot admits only when
``net_edge ≥ k × σ_transit`` (``drift_premium_k``; bot default 1.5).

σ is **stale-by-design** inventory from WHI-915
(``docs/references/m8-delay-decay.md``, lag=10m, session-split). Not
recomputed live. Premium is a hard bar, not an expected drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from monitor.metrics.edge import Direction
from monitor.metrics.session import SessionKind
from monitor.symbols.bstocks_models import BStocksPair
from monitor.symbols.models import Pair, SigmaTransitBps

SessionKey = Literal["open", "closed"]

_DIRECTIONS: tuple[Direction, Direction] = (
    "buy_fluxion_sell_bybit",
    "buy_bybit_sell_fluxion",
)


def session_key(session: SessionKind | str | None) -> SessionKey | None:
    """Normalize session to open/closed; None when unknown."""
    if session is None:
        return None
    if isinstance(session, SessionKind):
        return "open" if session is SessionKind.OPEN else "closed"
    s = str(session).lower()
    if s == "open":
        return "open"
    if s == "closed":
        return "closed"
    return None


def select_sigma_bps(
    sigma: SigmaTransitBps | None,
    session: SessionKind | str | None,
) -> Decimal | None:
    """Session-selected σ (bps). None when unmeasured or session unknown."""
    if sigma is None:
        return None
    key = session_key(session)
    if key is None:
        return None
    return sigma.open if key == "open" else sigma.closed


def drift_premium_bps(sigma_bps: Decimal | None, k: Decimal) -> Decimal | None:
    """``k × σ`` bar in bps. None when σ missing."""
    if sigma_bps is None:
        return None
    if k < 0:
        raise ValueError(f"drift_premium_k must be >= 0, got {k}")
    return k * sigma_bps


def clears_drift(
    net_bps: Decimal | None,
    premium_bps: Decimal | None,
) -> bool | None:
    """True when net ≥ premium. None when either side is unknown."""
    if net_bps is None or premium_bps is None:
        return None
    return net_bps >= premium_bps


def sigma_from_pair(pair: Pair | BStocksPair | object) -> SigmaTransitBps | None:
    """Inventory σ for Fluxion pairs; bStocks has no measured transit σ."""
    if isinstance(pair, Pair):
        return pair.sigma_transit_bps
    return None


@dataclass(frozen=True, slots=True)
class DriftAnnotation:
    """Wire-ready sequential-execution bar for one pair snapshot."""

    sigma_transit_bps: Decimal | None
    drift_premium_k: Decimal
    drift_premium_bps: Decimal | None
    session: SessionKey | None
    # Per-direction gate; None when net or σ unknown for that leg.
    clears_drift: dict[Direction, bool | None]
    # Convenience: gate for the overview optimal / Net direction.
    clears_drift_optimal: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sigma_transit_bps": (
                None if self.sigma_transit_bps is None else format(self.sigma_transit_bps, "f")
            ),
            "drift_premium_k": format(self.drift_premium_k, "f"),
            "drift_premium_bps": (
                None
                if self.drift_premium_bps is None
                else format(self.drift_premium_bps, "f")
            ),
            "session": self.session,
            "clears_drift": {
                d: self.clears_drift.get(d) for d in _DIRECTIONS
            },
            "clears_drift_optimal": self.clears_drift_optimal,
        }


def annotate_drift(
    *,
    sigma: SigmaTransitBps | None,
    session: SessionKind | str | None,
    k: Decimal,
    net_bps_by_direction: Mapping[str, Decimal | None] | None = None,
    optimal_direction: Direction | str | None = None,
    optimal_net_bps: Decimal | None = None,
) -> DriftAnnotation:
    """Build drift annotation from inventory σ + session + net edges.

    ``net_bps_by_direction`` keys are Direction strings. When only the
    overview optimal is known, pass ``optimal_direction`` + ``optimal_net_bps``
    (merged into the per-direction map).
    """
    sess = session_key(session)
    sigma_bps = select_sigma_bps(sigma, sess)
    premium = drift_premium_bps(sigma_bps, k)

    nets: dict[str, Decimal | None] = {
        d: None for d in _DIRECTIONS
    }
    if net_bps_by_direction:
        for d, v in net_bps_by_direction.items():
            if d in nets:
                nets[d] = v
    if optimal_direction is not None and optimal_direction in nets:
        # Prefer explicit optimal net when provided (same Q* as overview Net).
        if optimal_net_bps is not None or nets[str(optimal_direction)] is None:
            nets[str(optimal_direction)] = optimal_net_bps

    clears: dict[Direction, bool | None] = {
        d: clears_drift(nets[d], premium) for d in _DIRECTIONS
    }
    opt_clear: bool | None = None
    if optimal_direction is not None:
        opt_key = str(optimal_direction)
        if opt_key in clears:
            opt_clear = clears[opt_key]  # Direction keys are the Direction literals
    if opt_clear is None and optimal_net_bps is not None:
        opt_clear = clears_drift(optimal_net_bps, premium)

    return DriftAnnotation(
        sigma_transit_bps=sigma_bps,
        drift_premium_k=k,
        drift_premium_bps=premium,
        session=sess,
        clears_drift=clears,
        clears_drift_optimal=opt_clear,
    )


def drift_wire_for_pair(
    *,
    pair: Pair | BStocksPair | object,
    session: SessionKind | str | None,
    k: Decimal,
    optimal_direction: Direction | str | None = None,
    optimal_net_bps: Decimal | None = None,
    net_bps_by_direction: Mapping[str, Decimal | None] | None = None,
) -> dict[str, Any]:
    """JSON-ready drift fields for overview row / pair detail (WHI-962)."""
    return annotate_drift(
        sigma=sigma_from_pair(pair),
        session=session,
        k=k,
        net_bps_by_direction=net_bps_by_direction,
        optimal_direction=optimal_direction,
        optimal_net_bps=optimal_net_bps,
    ).to_dict()


def net_bps_from_pnl_tables(
    tables: Mapping[Any, Any] | None,
) -> dict[str, Decimal | None]:
    """Extract fillable optimal pnl_bps per direction from PnL bucket tables.

    Accepts either live ``PnlBucketTable`` objects or already-serialized dicts
    (``optimal.result.pnl_bps`` / ``optimal.pnl_usd`` path).
    """
    out: dict[str, Decimal | None] = {d: None for d in _DIRECTIONS}
    if not tables:
        return out
    for d in _DIRECTIONS:
        table = tables.get(d)
        if table is None:
            continue
        # Live object path
        optimal = getattr(table, "optimal", None)
        if optimal is not None:
            result = getattr(optimal, "result", None)
            if result is not None:
                bps = getattr(result, "pnl_bps", None)
                if bps is not None:
                    out[d] = bps if isinstance(bps, Decimal) else Decimal(str(bps))
                    continue
            # OptimalSizeResult.pnl_usd / q_star
            q = getattr(optimal, "q_star_usd", None)
            pnl = getattr(optimal, "pnl_usd", None)
            if q is not None and pnl is not None and q > 0:
                out[d] = (pnl / q) * Decimal(10_000)
                continue
        # Serialized dict path
        if isinstance(table, dict):
            opt = table.get("optimal")
            if not isinstance(opt, dict):
                continue
            result = opt.get("result")
            if isinstance(result, dict) and result.get("pnl_bps") is not None:
                out[d] = Decimal(str(result["pnl_bps"]))
                continue
            q_s, p_s = opt.get("q_star_usd"), opt.get("pnl_usd")
            if q_s is not None and p_s is not None:
                q = Decimal(str(q_s))
                if q > 0:
                    out[d] = (Decimal(str(p_s)) / q) * Decimal(10_000)
    return out


__all__ = [
    "DriftAnnotation",
    "annotate_drift",
    "clears_drift",
    "drift_premium_bps",
    "drift_wire_for_pair",
    "net_bps_from_pnl_tables",
    "select_sigma_bps",
    "session_key",
    "sigma_from_pair",
]
