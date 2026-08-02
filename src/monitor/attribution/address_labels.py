"""Productized address labeling: market_maker / rebalancer + persistence (WHI-768).

Builds on pure helpers in ``mm_draft`` and thresholds in ``AttributionConfig``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from monitor.attribution.config import AttributionConfig
from monitor.attribution.events import swap_notional_usd
from monitor.attribution.labels import BehaviorLabel
from monitor.attribution.mm_draft import (
    DraftAddressFeatures,
    DraftLabel,
    DraftThresholds,
    InventoryEvent,
    LedgerKind,
    TransferEdge,
    aggregate_address_features,
    assign_draft_label,
    count_cex_touches,
)
from monitor.quotes import Erc20TransferTick, FluxionRfqFillTick, FluxionSwapTick

if TYPE_CHECKING:
    from monitor.storage.store import SqliteStore


def draft_thresholds_from_config(config: AttributionConfig) -> DraftThresholds:
    mm = config.market_maker
    reb = config.rebalancer
    return DraftThresholds(
        mm_min_rfq_maker_fills=mm.min_rfq_maker_fills,
        mm_min_pairs=mm.min_pairs,
        mm_min_amm_trades=mm.min_amm_trades,
        mm_min_direction_share=mm.min_direction_share,
        mm_max_median_notional_usd=mm.max_median_notional_usd,
        mm_min_mean_reversion=mm.min_mean_reversion,
        reb_min_cex_touch_transfers=reb.min_cex_touch_transfers,
        reb_min_transfer_notional_native=reb.min_transfer_notional_native,
        arb_min_scored=config.arb_bot.min_scored_trades,
        arb_min_convergence=config.arb_bot.min_convergence_ratio,
        arb_min_bybit_align_ratio=config.arb_bot.min_bybit_align_ratio,
        pk_min_trades=config.price_keeper.min_trades,
        pk_min_direction_share=config.price_keeper.min_direction_share,
        pk_max_median_notional_usd=config.price_keeper.max_median_notional_usd,
        pk_max_trade_notional_usd=config.price_keeper.max_trade_notional_usd,
        retail_min_trades=config.retail.min_trades,
    )


_DRAFT_TO_BEHAVIOR: dict[DraftLabel, BehaviorLabel] = {
    DraftLabel.MARKET_MAKER: BehaviorLabel.MARKET_MAKER,
    DraftLabel.ARB_BOT: BehaviorLabel.ARB_BOT,
    DraftLabel.REBALANCER: BehaviorLabel.REBALANCER,
    DraftLabel.PRICE_KEEPER: BehaviorLabel.PRICE_KEEPER,
    DraftLabel.RETAIL: BehaviorLabel.RETAIL,
    DraftLabel.UNKNOWN: BehaviorLabel.UNKNOWN,
}


@dataclass(frozen=True, slots=True)
class AddressLabelResult:
    address: str
    label: BehaviorLabel
    reasons: tuple[str, ...]
    features: DraftAddressFeatures
    cex_touch_transfers: int
    is_rebalancer: bool  # orthogonal flag when CEX touches fire
    first_seen_ms: int | None
    last_seen_ms: int | None
    source: str  # auto | manual


@dataclass(frozen=True, slots=True)
class RebalanceEvent:
    address: str
    counterparty: str
    pair_id: str
    token: str
    amount: Decimal
    direction: str  # deposit_to_cex | withdraw_from_cex
    block_number: int
    block_ts: int
    recv_ts_ms: int
    tx_hash: str
    log_index: int


def inventory_events_from_ticks(
    *,
    swaps: Sequence[FluxionSwapTick] = (),
    rfq_fills: Sequence[FluxionRfqFillTick] = (),
    transfers: Sequence[Erc20TransferTick] = (),
    quote_is_token0_by_pair: Mapping[str, bool] | None = None,
) -> list[InventoryEvent]:
    """Lift collector ticks into signed inventory events for feature aggregation.

    Inventory convention (mm-attribution-analysis.md):
    - AMM: recipient buys native when direction=buy_native → +delta (stock units)
    - RFQ: maker sell_native → maker −stock; maker buy_native → maker +stock
    - Transfer: to +amount, from −amount (native only stream preferred)

    ``quote_is_token0_by_pair`` (UniV3 address order) is required for correct
    USDC notional and stock delta; when missing, the swap is skipped rather than
    inventing a pseudo-USD notional (see JournalReader / swap_notional_usd).
    """
    q0 = quote_is_token0_by_pair or {}
    out: list[InventoryEvent] = []
    for s in swaps:
        if s.direction not in ("buy_native", "sell_native"):
            continue
        if s.pair_id not in q0:
            continue
        quote0 = q0[s.pair_id]
        notional = swap_notional_usd(s, quote_is_token0=quote0)
        stock = abs(s.amount_token1 if quote0 else s.amount_token0)
        delta = stock if s.direction == "buy_native" else -stock
        out.append(
            InventoryEvent(
                ts_ms=s.block_ts * 1000,
                pair_id=s.pair_id,
                address=s.recipient.lower(),
                delta_native=delta,
                kind=LedgerKind.AMM_SWAP,
                tx_hash=s.tx_hash.lower(),
                direction=s.direction,
                notional_usd=notional,
                role="taker",
            )
        )
    for f in rfq_fills:
        if not f.maker:
            continue
        stock = (
            Decimal(f.stock_amount)
            if f.stock_amount is not None
            else Decimal(0)
        )
        usdc = (
            Decimal(f.usdc_amount) if f.usdc_amount is not None else Decimal(0)
        )
        if f.direction == "sell_native":
            maker_delta = -stock
            maker_dir = "sell_native"
        elif f.direction == "buy_native":
            maker_delta = stock
            maker_dir = "buy_native"
        else:
            maker_delta = Decimal(0)
            maker_dir = None
        pair = f.pair_id or "UNKNOWN"
        out.append(
            InventoryEvent(
                ts_ms=f.block_ts * 1000,
                pair_id=pair,
                address=f.maker.lower(),
                delta_native=maker_delta,
                kind=LedgerKind.RFQ_FILL,
                tx_hash=f.tx_hash.lower(),
                direction=maker_dir,
                notional_usd=usdc if usdc > 0 else None,
                role="maker",
            )
        )
        if f.taker:
            taker_delta = -maker_delta
            taker_dir = (
                "buy_native"
                if maker_dir == "sell_native"
                else ("sell_native" if maker_dir == "buy_native" else None)
            )
            out.append(
                InventoryEvent(
                    ts_ms=f.block_ts * 1000,
                    pair_id=pair,
                    address=f.taker.lower(),
                    delta_native=taker_delta,
                    kind=LedgerKind.RFQ_FILL,
                    tx_hash=f.tx_hash.lower(),
                    direction=taker_dir,
                    notional_usd=usdc if usdc > 0 else None,
                    role="taker",
                )
            )
    for t in transfers:
        out.append(
            InventoryEvent(
                ts_ms=t.block_ts * 1000,
                pair_id=t.pair_id,
                address=t.to_addr.lower(),
                delta_native=t.amount,
                kind=LedgerKind.ERC20_TRANSFER,
                tx_hash=t.tx_hash.lower(),
                role="transfer_counterparty",
                counterparty=t.frm.lower(),
            )
        )
        out.append(
            InventoryEvent(
                ts_ms=t.block_ts * 1000,
                pair_id=t.pair_id,
                address=t.frm.lower(),
                delta_native=-t.amount,
                kind=LedgerKind.ERC20_TRANSFER,
                tx_hash=t.tx_hash.lower(),
                role="transfer_counterparty",
                counterparty=t.to_addr.lower(),
            )
        )
    return out


def transfer_edges_from_ticks(
    transfers: Sequence[Erc20TransferTick],
) -> list[TransferEdge]:
    return [
        TransferEdge(
            ts_ms=t.block_ts * 1000,
            pair_id=t.pair_id,
            token=t.token.lower(),
            frm=t.frm.lower(),
            to=t.to_addr.lower(),
            amount=t.amount,
            tx_hash=t.tx_hash.lower(),
        )
        for t in transfers
    ]


def rebalance_events_from_transfers(
    transfers: Sequence[Erc20TransferTick],
    cex_wallets: Iterable[str],
    *,
    min_amount: Decimal = Decimal("0"),
) -> list[RebalanceEvent]:
    cex = {c.lower() for c in cex_wallets}
    out: list[RebalanceEvent] = []
    for t in transfers:
        if t.amount < min_amount:
            continue
        frm, to = t.frm.lower(), t.to_addr.lower()
        if to in cex and frm not in cex:
            out.append(
                RebalanceEvent(
                    address=frm,
                    counterparty=to,
                    pair_id=t.pair_id,
                    token=t.token.lower(),
                    amount=t.amount,
                    direction="deposit_to_cex",
                    block_number=t.block_number,
                    block_ts=t.block_ts,
                    recv_ts_ms=t.recv_ts_ms,
                    tx_hash=t.tx_hash.lower(),
                    log_index=t.log_index,
                )
            )
        elif frm in cex and to not in cex:
            out.append(
                RebalanceEvent(
                    address=to,
                    counterparty=frm,
                    pair_id=t.pair_id,
                    token=t.token.lower(),
                    amount=t.amount,
                    direction="withdraw_from_cex",
                    block_number=t.block_number,
                    block_ts=t.block_ts,
                    recv_ts_ms=t.recv_ts_ms,
                    tx_hash=t.tx_hash.lower(),
                    log_index=t.log_index,
                )
            )
    return out


def assign_address_label(
    features: DraftAddressFeatures,
    config: AttributionConfig,
    *,
    cex_touch_transfers: int = 0,
    first_seen_ms: int | None = None,
    last_seen_ms: int | None = None,
    overrides: Mapping[str, str] | None = None,
) -> AddressLabelResult:
    """Priority: manual override → market_maker → arb_bot → rebalancer → …

    ``is_rebalancer`` is set whenever CEX-touch gate fires, even if primary
    label is market_maker / arb_bot (orthogonal flag per research note).
    """
    addr = features.address.lower()
    ovr = {k.lower(): v for k, v in (overrides or {}).items()}
    # Config overrides map
    for item in config.address_overrides:
        ovr.setdefault(item.address.lower(), item.label)

    if addr in ovr:
        try:
            label = BehaviorLabel(ovr[addr])
        except ValueError:
            label = BehaviorLabel.UNKNOWN
        return AddressLabelResult(
            address=addr,
            label=label,
            reasons=("manual_override",),
            features=features,
            cex_touch_transfers=cex_touch_transfers,
            is_rebalancer=cex_touch_transfers
            >= config.rebalancer.min_cex_touch_transfers,
            first_seen_ms=first_seen_ms,
            last_seen_ms=last_seen_ms,
            source="manual",
        )

    th = draft_thresholds_from_config(config)
    labeled = assign_draft_label(
        features, thresholds=th, cex_touch_transfers=cex_touch_transfers
    )
    label = _DRAFT_TO_BEHAVIOR[labeled.label]
    is_reb = cex_touch_transfers >= config.rebalancer.min_cex_touch_transfers
    return AddressLabelResult(
        address=addr,
        label=label,
        reasons=labeled.reasons,
        features=features,
        cex_touch_transfers=cex_touch_transfers,
        is_rebalancer=is_reb,
        first_seen_ms=first_seen_ms,
        last_seen_ms=last_seen_ms,
        source="auto",
    )


def label_addresses_from_journal(
    *,
    swaps: Sequence[FluxionSwapTick] = (),
    rfq_fills: Sequence[FluxionRfqFillTick] = (),
    transfers: Sequence[Erc20TransferTick] = (),
    config: AttributionConfig,
    contract_flags: Mapping[str, bool] | None = None,
    quote_is_token0_by_pair: Mapping[str, bool] | None = None,
) -> list[AddressLabelResult]:
    """End-to-end label pass over collector ticks (pure; no I/O)."""
    events = inventory_events_from_ticks(
        swaps=swaps,
        rfq_fills=rfq_fills,
        transfers=transfers,
        quote_is_token0_by_pair=quote_is_token0_by_pair,
    )
    edges = transfer_edges_from_ticks(transfers)
    by_addr: dict[str, list[InventoryEvent]] = {}
    for e in events:
        by_addr.setdefault(e.address.lower(), []).append(e)
    flags = {k.lower(): v for k, v in (contract_flags or {}).items()}
    min_amt = config.rebalancer.min_transfer_notional_native
    cex = config.rebalancer.cex_wallets
    results: list[AddressLabelResult] = []
    for addr in sorted(by_addr):
        addr_events = by_addr[addr]
        feats = aggregate_address_features(
            addr_events, address=addr, is_contract=flags.get(addr)
        )
        touches = count_cex_touches(edges, addr, cex, min_amount=min_amt)
        first_ms = min((e.ts_ms for e in addr_events), default=None)
        last_ms = max((e.ts_ms for e in addr_events), default=None)
        results.append(
            assign_address_label(
                feats,
                config,
                cex_touch_transfers=touches,
                first_seen_ms=first_ms,
                last_seen_ms=last_ms,
            )
        )
    results.sort(key=lambda r: (r.label.value, r.address))
    return results


def persist_address_labels(
    store: SqliteStore,
    results: Sequence[AddressLabelResult],
    *,
    updated_at_ms: int,
) -> int:
    """Write auto labels into SqliteStore (skips sticky manual rows)."""
    n = 0
    for r in results:
        if r.source == "manual":
            store.upsert_manual_address_label(
                address=r.address,
                label=r.label.value,
                evidence_summary="; ".join(r.reasons),
                updated_at_ms=updated_at_ms,
            )
            n += 1
            continue
        store.upsert_address_label(
            address=r.address,
            label=r.label.value,
            evidence_summary="; ".join(r.reasons),
            first_seen_ms=r.first_seen_ms,
            last_seen_ms=r.last_seen_ms,
            source="auto",
            is_rebalancer=r.is_rebalancer,
            n_rfq_maker=r.features.n_rfq_maker,
            n_amm=r.features.n_amm,
            cex_touch_transfers=r.cex_touch_transfers,
            updated_at_ms=updated_at_ms,
        )
        n += 1
    return n


def persist_rebalance_events(
    store: SqliteStore, events: Sequence[RebalanceEvent]
) -> int:
    rows = [
        (
            e.address,
            e.counterparty,
            e.pair_id,
            e.token,
            str(e.amount),
            e.direction,
            e.block_number,
            e.block_ts,
            e.recv_ts_ms,
            e.tx_hash,
            e.log_index,
        )
        for e in events
    ]
    return store.insert_rebalance_events(rows)
