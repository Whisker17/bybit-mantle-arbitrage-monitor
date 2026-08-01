"""M5-facing multi-pair attribution snapshot."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from monitor.attribution.addresses import AddressRole
from monitor.attribution.aggregate import (
    MechanismShare,
    PairAttribution,
    SessionFilter,
    build_global_mechanism_share,
    build_pair_attribution,
    filter_amm_session,
    filter_rfq_session,
    window_bounds_ms,
)
from monitor.attribution.config import AttributionConfig
from monitor.attribution.events import AmmTradeEvent, RfqFillEvent


@dataclass(frozen=True, slots=True)
class AttributionSnapshot:
    """Full attribution state for the live panel (all pairs + global mechanism).

    M5 secondary pages take ``pairs[pair_id]``; the overview can show
    ``global_mechanism`` RFQ vs AMM mix without per-pair RFQ enrichment.
    """

    session: SessionFilter
    global_mechanism: MechanismShare
    pairs: dict[str, PairAttribution]
    window_start_ms: int | None
    window_end_ms: int | None


def build_attribution_snapshot(
    *,
    amm_trades: Sequence[AmmTradeEvent],
    rfq_fills: Sequence[RfqFillEvent] = (),
    config: AttributionConfig,
    pair_ids: Sequence[str] | None = None,
    session: SessionFilter = "all",
    contract_flags: Mapping[str, bool] | None = None,
    roles: Mapping[str, AddressRole | str] | None = None,
) -> AttributionSnapshot:
    """Build per-pair attribution + global mechanism share.

    If ``pair_ids`` is None, discover pairs from AMM trades (and RFQ fills that
    carry a pair_id). Global mechanism share and window bounds respect
    ``session`` the same way pair panels do.
    """
    if pair_ids is None:
        discovered: set[str] = {t.pair_id for t in amm_trades}
        discovered |= {f.pair_id for f in rfq_fills if f.pair_id}
        pair_ids = sorted(discovered)

    pairs: dict[str, PairAttribution] = {}
    for pid in pair_ids:
        pairs[pid] = build_pair_attribution(
            pair_id=pid,
            amm_trades=amm_trades,
            rfq_fills=rfq_fills,
            config=config,
            session=session,
            contract_flags=contract_flags,
            roles=roles,
        )

    amm_scoped = filter_amm_session(amm_trades, session)
    rfq_scoped = filter_rfq_session(rfq_fills, session)
    global_m = build_global_mechanism_share(amm_scoped, rfq_scoped)
    start, end = window_bounds_ms(amm_scoped, rfq_scoped)
    return AttributionSnapshot(
        session=session,
        global_mechanism=global_m,
        pairs=pairs,
        window_start_ms=start,
        window_end_ms=end,
    )
