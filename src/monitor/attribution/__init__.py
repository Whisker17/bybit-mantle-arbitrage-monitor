"""Attribution: mechanism (RFQ/AMM) + behavior labels (M4 / WHI-733 / WHI-768).

Public seams (tests and M5 TUI depend on these, not internals):

- ``load_attribution_config`` — typed ``config/attribution.yaml``
- ``Mechanism`` / ``mechanism_for_swap`` / ``mechanism_for_rfq_fill``
- ``is_converging`` / ``bybit_move_aligned`` — per-trade features
- ``classify_addresses`` — eth_getCode contract vs EOA
- ``BehaviorLabel`` / ``assign_behavior_label`` / ``label_takers`` (AMM takers)
- ``assign_address_label`` / ``label_addresses_from_journal`` (WHI-768 full path)
- ``build_pair_attribution`` / ``build_attribution_snapshot`` — M5 panel model
- ``AmmTradeEvent`` / ``RfqFillEvent`` / builders from ``monitor.quotes``

Label rules: ``docs/references/m4-attribution-labels.md`` +
``docs/references/mm-attribution-analysis.md``.
Depends on ``monitor.quotes`` + ``monitor.metrics.session``; RPC only for
optional ``classify_addresses``.
"""

from monitor.attribution.address_labels import (
    AddressLabelResult,
    RebalanceEvent,
    assign_address_label,
    draft_thresholds_from_config,
    inventory_events_from_ticks,
    label_addresses_from_journal,
    persist_address_labels,
    persist_rebalance_events,
    rebalance_events_from_transfers,
)
from monitor.attribution.addresses import (
    AddressRole,
    BatchRpc,
    classify_addresses,
    classify_addresses_from_config,
    is_contract_code,
    probe_roles,
    probe_roles_from_config,
    role_samples_from_trades,
)
from monitor.attribution.aggregate import (
    MechanismShare,
    PairAttribution,
    SessionFilter,
    TakerRow,
    build_global_mechanism_share,
    build_pair_attribution,
    mechanism_of_trade,
    mechanism_share,
    window_bounds_ms,
)
from monitor.attribution.config import (
    AttributionConfig,
    AttributionConfigError,
    default_attribution_path,
    load_attribution_config,
)
from monitor.attribution.convergence import (
    bybit_move_aligned,
    convergence_share,
    is_converging,
    resolve_bybit_mid_prev,
)
from monitor.attribution.events import (
    AmmTradeEvent,
    RfqFillEvent,
    amm_trade_from_swap,
    rfq_fill_from_tick,
    swap_notional_usd,
)
from monitor.attribution.labels import (
    ActivityRegime,
    AddressFeatures,
    BehaviorLabel,
    TakerProfile,
    activity_regime,
    assign_behavior_label,
    compute_address_features,
    label_takers,
)
from monitor.attribution.mechanism import (
    Mechanism,
    mechanism_for_rfq_fill,
    mechanism_for_swap,
)
from monitor.attribution.snapshot import AttributionSnapshot, build_attribution_snapshot

__all__ = [
    "ActivityRegime",
    "AddressFeatures",
    "AddressLabelResult",
    "AddressRole",
    "AmmTradeEvent",
    "AttributionConfig",
    "AttributionConfigError",
    "AttributionSnapshot",
    "BatchRpc",
    "BehaviorLabel",
    "Mechanism",
    "MechanismShare",
    "PairAttribution",
    "RebalanceEvent",
    "RfqFillEvent",
    "SessionFilter",
    "TakerProfile",
    "TakerRow",
    "activity_regime",
    "amm_trade_from_swap",
    "assign_address_label",
    "assign_behavior_label",
    "build_attribution_snapshot",
    "build_global_mechanism_share",
    "build_pair_attribution",
    "bybit_move_aligned",
    "classify_addresses",
    "classify_addresses_from_config",
    "compute_address_features",
    "convergence_share",
    "default_attribution_path",
    "draft_thresholds_from_config",
    "inventory_events_from_ticks",
    "is_contract_code",
    "is_converging",
    "label_addresses_from_journal",
    "label_takers",
    "load_attribution_config",
    "mechanism_for_rfq_fill",
    "mechanism_for_swap",
    "mechanism_of_trade",
    "mechanism_share",
    "persist_address_labels",
    "persist_rebalance_events",
    "probe_roles",
    "probe_roles_from_config",
    "rebalance_events_from_transfers",
    "resolve_bybit_mid_prev",
    "rfq_fill_from_tick",
    "role_samples_from_trades",
    "swap_notional_usd",
    "window_bounds_ms",
]
