"""Draft MM / rebalancer attribution helpers (WHI-767 research).

Pure functions over in-memory events. Threshold defaults match
``docs/references/mm-attribution-analysis.md`` rule draft; production
wiring + config live in follow-on WHI-768.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from eth_utils import keccak  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class LedgerKind(StrEnum):
    AMM_SWAP = "amm_swap"
    RFQ_FILL = "rfq_fill"
    ERC20_TRANSFER = "erc20_transfer"


@dataclass(frozen=True, slots=True)
class InventoryEvent:
    """Signed inventory change for one address × pair (native-equivalent units).

    Positive ``delta_native`` = address inventory of the xStock increased.
    """

    ts_ms: int
    pair_id: str
    address: str
    delta_native: Decimal
    kind: LedgerKind
    tx_hash: str
    # Optional trade context (AMM/RFQ only)
    direction: str | None = None  # buy_native | sell_native | None
    notional_usd: Decimal | None = None
    converges: bool | None = None
    bybit_align: bool | None = None  # direction vs Bybit mid Δ (lead-lag)
    session: str | None = None  # open | closed
    role: str | None = None  # taker | maker | transfer_counterparty
    counterparty: str | None = None


@dataclass(frozen=True, slots=True)
class PositionSeries:
    address: str
    pair_id: str
    events: tuple[InventoryEvent, ...]
    # cumulative after each event (same length as events)
    inventory: tuple[Decimal, ...]

    @property
    def final_inventory(self) -> Decimal:
        return self.inventory[-1] if self.inventory else Decimal(0)


@dataclass(frozen=True, slots=True)
class AddressPairFeatures:
    address: str
    pair_id: str
    n_events: int
    n_amm: int
    n_rfq_maker: int
    n_rfq_taker: int
    n_transfer: int
    n_buy: int
    n_sell: int
    notional_usd: Decimal
    median_notional_usd: Decimal
    max_notional_usd: Decimal
    open_share: float | None
    closed_share: float | None
    convergence_ratio: float | None
    n_convergence_scored: int
    # inventory path stats
    inv_mean_reversion: float | None  # fraction of steps that move toward 0
    final_inventory: Decimal


@dataclass(frozen=True, slots=True)
class DraftAddressFeatures:
    address: str
    n_pairs: int
    n_amm: int
    n_rfq_maker: int
    n_rfq_taker: int
    n_transfer: int
    n_buy: int
    n_sell: int
    notional_usd: Decimal
    median_notional_usd: Decimal
    max_notional_usd: Decimal
    open_share: float | None
    closed_share: float | None
    convergence_ratio: float | None
    n_convergence_scored: int
    bybit_align_ratio: float | None
    n_bybit_align_scored: int
    both_directions: bool
    # cross-pair
    pairs: tuple[str, ...]
    # inventory mean-reversion averaged over pairs with ≥2 events
    inv_mean_reversion: float | None
    is_contract: bool | None = None


class DraftLabel(StrEnum):
    """Draft labels for WHI-767; orthogonal to M4 BehaviorLabel until productized."""

    MARKET_MAKER = "market_maker"
    REBALANCER = "rebalancer"
    ARB_BOT = "arb_bot"
    PRICE_KEEPER = "price_keeper"
    RETAIL = "retail"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DraftThresholds:
    """Machine-checkable gates (documented in mm-attribution-analysis.md)."""

    # market_maker — RFQ fill count is sparse on xStocks; 2 is the research
    # default (30d sample had ≤2 fills/maker). Raise for production if needed.
    mm_min_rfq_maker_fills: int = 2
    mm_min_pairs: int = 2
    mm_min_amm_both_dirs: int = 10
    mm_min_direction_share: float = 0.25
    mm_max_median_notional_usd: Decimal = Decimal("500")
    mm_min_mean_reversion: float = 0.55
    # rebalancer
    reb_min_cex_touch_transfers: int = 3
    reb_min_transfer_notional_native: Decimal = Decimal("1")
    # arb_bot / price_keeper / retail — match config/attribution.yaml (M4)
    arb_min_scored: int = 20
    arb_min_convergence: float = 0.80
    arb_min_bybit_align_ratio: float = 0.0  # 0 = do not gate (M4 default)
    pk_min_trades: int = 10
    pk_min_direction_share: float = 0.25
    pk_max_median_notional_usd: Decimal = Decimal("500")
    pk_max_trade_notional_usd: Decimal = Decimal("2000")
    retail_min_trades: int = 5


@dataclass(frozen=True, slots=True)
class LabeledAddress:
    features: DraftAddressFeatures
    label: DraftLabel
    reasons: tuple[str, ...]


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def build_position_series(
    events: Sequence[InventoryEvent],
    *,
    address: str,
    pair_id: str,
) -> PositionSeries:
    """Cumulative signed inventory for one (address, pair)."""
    addr = address.lower()
    pid = pair_id
    rows = sorted(
        (e for e in events if e.address.lower() == addr and e.pair_id == pid),
        key=lambda e: (e.ts_ms, e.tx_hash, str(e.kind)),
    )
    inv: list[Decimal] = []
    running = Decimal(0)
    for e in rows:
        running += e.delta_native
        inv.append(running)
    return PositionSeries(
        address=addr,
        pair_id=pid,
        events=tuple(rows),
        inventory=tuple(inv),
    )


def inventory_mean_reversion(inventory: Sequence[Decimal]) -> float | None:
    """Share of steps whose |inv| decreased (toward zero).

    Requires ≥2 points. A pure trend (always growing) scores near 0; oscillation
    around zero scores high. Zero-inventory flatlines score None.
    """
    if len(inventory) < 2:
        return None
    steps = 0
    toward = 0
    for prev, cur in zip(inventory, inventory[1:], strict=False):
        prev_abs = abs(prev)
        cur_abs = abs(cur)
        if prev_abs == 0 and cur_abs == 0:
            continue
        steps += 1
        if cur_abs < prev_abs:
            toward += 1
    if steps == 0:
        return None
    return toward / steps


def _median_decimal(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def _ratio(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return num / den


def compute_pair_features(series: PositionSeries) -> AddressPairFeatures:
    ev = series.events
    n = len(ev)
    n_amm = sum(1 for e in ev if e.kind is LedgerKind.AMM_SWAP)
    n_rfq_m = sum(
        1 for e in ev if e.kind is LedgerKind.RFQ_FILL and e.role == "maker"
    )
    n_rfq_t = sum(
        1 for e in ev if e.kind is LedgerKind.RFQ_FILL and e.role == "taker"
    )
    n_xfer = sum(1 for e in ev if e.kind is LedgerKind.ERC20_TRANSFER)
    n_buy = sum(1 for e in ev if e.direction == "buy_native")
    n_sell = sum(1 for e in ev if e.direction == "sell_native")
    notionals = [e.notional_usd for e in ev if e.notional_usd is not None]
    notional_sum = sum(notionals, start=Decimal(0))
    med = _median_decimal(notionals)
    max_n = max(notionals) if notionals else Decimal(0)

    trade_ev = [
        e for e in ev if e.kind in (LedgerKind.AMM_SWAP, LedgerKind.RFQ_FILL)
    ]
    n_open = sum(1 for e in trade_ev if e.session == "open")
    n_closed = sum(1 for e in trade_ev if e.session == "closed")
    sess_den = n_open + n_closed

    conv_hits = sum(1 for e in ev if e.converges is True)
    conv_scored = sum(1 for e in ev if e.converges is not None)
    inv_mr = inventory_mean_reversion(series.inventory)

    return AddressPairFeatures(
        address=series.address,
        pair_id=series.pair_id,
        n_events=n,
        n_amm=n_amm,
        n_rfq_maker=n_rfq_m,
        n_rfq_taker=n_rfq_t,
        n_transfer=n_xfer,
        n_buy=n_buy,
        n_sell=n_sell,
        notional_usd=notional_sum,
        median_notional_usd=med,
        max_notional_usd=max_n,
        open_share=_ratio(n_open, sess_den),
        closed_share=_ratio(n_closed, sess_den),
        convergence_ratio=_ratio(conv_hits, conv_scored),
        n_convergence_scored=conv_scored,
        inv_mean_reversion=inv_mr,
        final_inventory=series.final_inventory,
    )


def aggregate_address_features(
    events: Sequence[InventoryEvent],
    *,
    address: str,
    is_contract: bool | None = None,
) -> DraftAddressFeatures:
    """Aggregate features from raw ledger events (correct median / session)."""
    addr = address.lower()
    rows = [e for e in events if e.address.lower() == addr]
    pairs = tuple(sorted({e.pair_id for e in rows}))
    n_amm = sum(1 for e in rows if e.kind is LedgerKind.AMM_SWAP)
    n_rfq_m = sum(
        1 for e in rows if e.kind is LedgerKind.RFQ_FILL and e.role == "maker"
    )
    n_rfq_t = sum(
        1 for e in rows if e.kind is LedgerKind.RFQ_FILL and e.role == "taker"
    )
    n_xfer = sum(1 for e in rows if e.kind is LedgerKind.ERC20_TRANSFER)
    n_buy = sum(1 for e in rows if e.direction == "buy_native")
    n_sell = sum(1 for e in rows if e.direction == "sell_native")
    notionals = [e.notional_usd for e in rows if e.notional_usd is not None]
    notional = sum(notionals, start=Decimal(0))
    trade_ev = [
        e for e in rows if e.kind in (LedgerKind.AMM_SWAP, LedgerKind.RFQ_FILL)
    ]
    n_open = sum(1 for e in trade_ev if e.session == "open")
    n_closed = sum(1 for e in trade_ev if e.session == "closed")
    sess_den = n_open + n_closed
    conv_hits = sum(1 for e in rows if e.converges is True)
    conv_scored = sum(1 for e in rows if e.converges is not None)
    align_hits = sum(1 for e in rows if e.bybit_align is True)
    align_scored = sum(1 for e in rows if e.bybit_align is not None)
    mrs: list[float] = []
    for pid in pairs:
        series = build_position_series(rows, address=addr, pair_id=pid)
        mr = inventory_mean_reversion(series.inventory)
        if mr is not None:
            mrs.append(mr)
    return DraftAddressFeatures(
        address=addr,
        n_pairs=len(pairs),
        n_amm=n_amm,
        n_rfq_maker=n_rfq_m,
        n_rfq_taker=n_rfq_t,
        n_transfer=n_xfer,
        n_buy=n_buy,
        n_sell=n_sell,
        notional_usd=notional,
        median_notional_usd=_median_decimal(notionals),
        max_notional_usd=max(notionals) if notionals else Decimal(0),
        open_share=_ratio(n_open, sess_den),
        closed_share=_ratio(n_closed, sess_den),
        convergence_ratio=_ratio(conv_hits, conv_scored),
        n_convergence_scored=conv_scored,
        bybit_align_ratio=_ratio(align_hits, align_scored),
        n_bybit_align_scored=align_scored,
        both_directions=n_buy > 0 and n_sell > 0,
        pairs=pairs,
        inv_mean_reversion=(sum(mrs) / len(mrs)) if mrs else None,
        is_contract=is_contract,
    )


def assign_draft_label(
    features: DraftAddressFeatures,
    *,
    thresholds: DraftThresholds | None = None,
    cex_touch_transfers: int = 0,
) -> LabeledAddress:
    """Priority: market_maker → arb_bot → rebalancer → price_keeper → retail → unknown.

    ``cex_touch_transfers`` is the count of native/wrapper transfers whose
    counterparty is a known or clustered CEX hot wallet (computed outside).
    Arb-bot is intentionally *before* rebalancer so high-convergence peg
    traders are not swallowed by deposit-touch noise.
    """
    th = thresholds or DraftThresholds()
    reasons: list[str] = []
    n_trade = features.n_amm + features.n_rfq_maker + features.n_rfq_taker

    # --- market_maker ---
    mm_hit = False
    if features.n_rfq_maker >= th.mm_min_rfq_maker_fills:
        mm_hit = True
        reasons.append(
            f"rfq_maker_fills={features.n_rfq_maker}>={th.mm_min_rfq_maker_fills}"
        )
    if (
        features.n_pairs >= th.mm_min_pairs
        and features.n_amm >= th.mm_min_amm_both_dirs
        and features.both_directions
    ):
        buy_share = features.n_buy / features.n_amm if features.n_amm else 0.0
        sell_share = features.n_sell / features.n_amm if features.n_amm else 0.0
        if (
            buy_share >= th.mm_min_direction_share
            and sell_share >= th.mm_min_direction_share
            and features.median_notional_usd <= th.mm_max_median_notional_usd
            and (
                features.inv_mean_reversion is None
                or features.inv_mean_reversion >= th.mm_min_mean_reversion
            )
        ):
            mm_hit = True
            reasons.append(
                f"cross_pair_bidirectional pairs={features.n_pairs} "
                f"amm={features.n_amm} med_notional={features.median_notional_usd} "
                f"mean_rev={features.inv_mean_reversion}"
            )
    if mm_hit:
        return LabeledAddress(features, DraftLabel.MARKET_MAKER, tuple(reasons))

    # --- arb_bot ---
    if (
        features.n_convergence_scored >= th.arb_min_scored
        and features.convergence_ratio is not None
        and features.convergence_ratio >= th.arb_min_convergence
    ):
        align_ok = (
            th.arb_min_bybit_align_ratio <= 0
            or features.bybit_align_ratio is None
            or features.bybit_align_ratio >= th.arb_min_bybit_align_ratio
        )
        if align_ok:
            reasons.append(
                f"convergence={features.convergence_ratio:.2f} "
                f"on {features.n_convergence_scored} scored"
            )
            if features.bybit_align_ratio is not None:
                reasons.append(f"bybit_align={features.bybit_align_ratio:.2f}")
            return LabeledAddress(features, DraftLabel.ARB_BOT, tuple(reasons))

    # --- rebalancer ---
    if cex_touch_transfers >= th.reb_min_cex_touch_transfers:
        reasons.append(f"cex_touch_transfers={cex_touch_transfers}")
        return LabeledAddress(features, DraftLabel.REBALANCER, tuple(reasons))

    # --- price_keeper ---
    if features.n_amm >= th.pk_min_trades and features.both_directions:
        buy_share = features.n_buy / features.n_amm
        sell_share = features.n_sell / features.n_amm
        if (
            buy_share >= th.pk_min_direction_share
            and sell_share >= th.pk_min_direction_share
            and features.median_notional_usd <= th.pk_max_median_notional_usd
            and features.max_notional_usd <= th.pk_max_trade_notional_usd
        ):
            reasons.append(
                f"small bidirectional amm n={features.n_amm} "
                f"med={features.median_notional_usd}"
            )
            return LabeledAddress(features, DraftLabel.PRICE_KEEPER, tuple(reasons))

    # --- retail ---
    if n_trade >= th.retail_min_trades:
        reasons.append(f"n_trade={n_trade}")
        return LabeledAddress(features, DraftLabel.RETAIL, tuple(reasons))

    reasons.append("thin_sample")
    return LabeledAddress(features, DraftLabel.UNKNOWN, tuple(reasons))


# ---------------------------------------------------------------------------
# RFQ receipt decode (pure over already-fetched logs)
# ---------------------------------------------------------------------------


TOPIC_TRANSFER = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
TOPIC_APPROVAL = "0x" + keccak(text="Approval(address,address,uint256)").hex()


def _topic_addr(topic: str) -> str:
    h = topic[2:] if topic.startswith("0x") else topic
    return "0x" + h[-40:].lower()


def _topic0(log: Mapping[str, object]) -> str:
    topics = log.get("topics") or []
    if not topics:
        return ""
    t0 = topics[0]  # type: ignore[index]
    if isinstance(t0, (bytes, bytearray)):
        return "0x" + bytes(t0).hex()
    return str(t0).lower()


@dataclass(frozen=True, slots=True)
class DecodedRfqFill:
    """Best-effort maker/taker + legs from a LOP fill receipt."""

    tx_hash: str
    maker: str | None
    taker: str | None
    pair_id: str | None
    token: str | None  # xStock native or wrapper involved
    usdc_amount: Decimal | None
    stock_amount: Decimal | None
    # maker sells stock (provides stock) → taker buys stock
    maker_side: str | None  # sell_native | buy_native
    external_addresses: tuple[str, ...]
    notes: tuple[str, ...]


def decode_rfq_fill_from_receipt(
    *,
    tx_hash: str,
    logs: Sequence[Mapping[str, object]],
    usdc: str,
    lop: str,
    settlement_router: str | None,
    token_to_pair: Mapping[str, str],
    known_infra: Iterable[str] = (),
) -> DecodedRfqFill:
    """Extract maker/taker from Transfer graph around a LOP OrderFilled.

    Heuristic (Fluxion LOP settlement observed on Mantle):
    - USDC and xStock (native or wrapper) Transfer logs form a path through
      a settlement router.
    - The **external** address that *sends* the making asset (first non-infra
      stock sender, or Approval→LOP owner) is the **maker**.
    - The external address that *receives* the taking asset (final non-infra
      receiver of the other leg) is the **taker**.
    - Fallback: ``tx.from`` is often a keeper/relayer, not the economic maker —
      do not use it as maker.
    """
    usdc_l = usdc.lower()
    lop_l = lop.lower()
    router_l = (settlement_router or "").lower()
    infra = {a.lower() for a in known_infra}
    infra |= {usdc_l, lop_l}
    if router_l:
        infra.add(router_l)
    # fee / intermediate contracts seen repeatedly on Fluxion LOP paths
    infra |= {
        "0x5f7a4c11bde4f218f0025ef444c369d838ffa2ad",
        "0x3fdcb4af7f9019f6f8b669790bde93bbf91bbcc0",
        "0x68a359928fa70d17207c5d7e47a9047c0744d6f4",
        "0x788d911ae7c95121a89a0f0306db65d87422e1de",
        "0x41dee1855293e4450cd67459047f372d4d818143",
    }

    notes: list[str] = []
    stock_transfers: list[tuple[str, str, str, Decimal]] = []  # token, from, to, amt
    usdc_transfers: list[tuple[str, str, Decimal]] = []
    approvals_to_lop: list[str] = []

    for log in logs:
        addr = str(log.get("address") or "").lower()
        t0 = _topic0(log)
        raw_topics = log.get("topics") or []
        if not isinstance(raw_topics, (list, tuple)):
            continue
        topics = [str(t) for t in raw_topics]
        data = str(log.get("data") or "0x")
        if t0 == TOPIC_APPROVAL.lower() and len(topics) >= 3:
            owner = _topic_addr(topics[1])
            spender = _topic_addr(topics[2])
            if spender == lop_l:
                approvals_to_lop.append(owner)
            continue
        if t0 != TOPIC_TRANSFER.lower() or len(topics) < 3:
            continue
        frm = _topic_addr(topics[1])
        to = _topic_addr(topics[2])
        try:
            raw = bytes.fromhex(data[2:] if data.startswith("0x") else data)
            amt = Decimal(int.from_bytes(raw[-32:], "big")) if len(raw) >= 32 else Decimal(0)
        except ValueError:
            amt = Decimal(0)
        if addr == usdc_l:
            # USDC 6 decimals
            usdc_transfers.append((frm, to, amt / Decimal(1_000_000)))
        else:
            # Inventory xStock OR any non-USDC ERC-20 on the path (18-dec assumption
            # for Backed xStocks / wrappers; unmapped tokens still yield maker/taker).
            stock_transfers.append((addr, frm, to, amt / Decimal(10**18)))

    pair_id: str | None = None
    token: str | None = None
    if stock_transfers:
        # Prefer a token we can map to inventory pair_id.
        mapped = [t for t in stock_transfers if t[0] in token_to_pair]
        chosen = mapped[0] if mapped else stock_transfers[0]
        token = chosen[0]
        pair_id = token_to_pair.get(token)
        if pair_id is None:
            notes.append(f"unmapped_stock_token={token}")

    external: set[str] = set()
    for _tok, frm, to, _a in stock_transfers:
        if frm not in infra and frm != "0x" + "0" * 40:
            external.add(frm)
        if to not in infra and to != "0x" + "0" * 40:
            external.add(to)
    for frm, to, _a in usdc_transfers:
        if frm not in infra and frm != "0x" + "0" * 40:
            external.add(frm)
        if to not in infra and to != "0x" + "0" * 40:
            external.add(to)

    maker: str | None = None
    taker: str | None = None
    maker_side: str | None = None

    zero = "0x" + "0" * 40
    external.discard(zero)

    # Maker via Approval to LOP (strongest when present)
    if approvals_to_lop:
        maker = approvals_to_lop[0]
        notes.append("maker_from_approval_to_lop")

    # Maker = first external stock sender (provides inventory)
    if maker is None:
        for _tok, frm, _to, _a in stock_transfers:
            if frm not in infra and frm != zero:
                maker = frm
                notes.append("maker_from_first_stock_sender")
                break

    # Fallback: sole external on the whole receipt (common when token unmapped
    # earlier and only USDC path was visible — now stock_transfers is broader).
    if maker is None and len(external) == 1:
        maker = next(iter(external))
        notes.append("maker_sole_external")
    elif maker is None and external:
        # Prefer external that sends USDC (taker buy) is NOT maker; maker often
        # receives USDC. First external USDC receiver as maker when stock silent.
        usdc_recv = [
            to for frm, to, _a in usdc_transfers if to not in infra and to != zero
        ]
        if usdc_recv:
            maker = usdc_recv[-1]  # final external USDC sink often the maker
            notes.append("maker_from_final_usdc_receiver")

    # Taker = external that is not maker and appears on the opposite leg
    if maker is not None:
        others = [a for a in external if a != maker]
        if len(others) == 1:
            taker = others[0]
            notes.append("taker_sole_other_external")
        elif others:
            # Prefer external that receives stock (buy) or sends USDC (buy)
            stock_receivers = {
                to
                for _t, frm, to, _a in stock_transfers
                if to not in infra and to != maker and to != zero
            }
            usdc_senders = {
                frm
                for frm, to, _a in usdc_transfers
                if frm not in infra and frm != maker and frm != zero
            }
            buy_cands = stock_receivers | usdc_senders
            if buy_cands:
                taker = sorted(buy_cands)[0]
                notes.append("taker_from_buy_side_external")
            else:
                taker = sorted(others)[0]
                notes.append("taker_first_other_external")

    # Direction: if maker sends stock → maker sell_native; if maker sends USDC → buy
    if maker is not None:
        maker_sends_stock = any(
            frm == maker for _t, frm, _to, _a in stock_transfers
        )
        maker_sends_usdc = any(frm == maker for frm, _to, _a in usdc_transfers)
        if maker_sends_stock and not maker_sends_usdc:
            maker_side = "sell_native"
        elif maker_sends_usdc and not maker_sends_stock:
            maker_side = "buy_native"
        elif maker_sends_stock:
            maker_side = "sell_native"
            notes.append("ambiguous_side_default_sell")
        else:
            notes.append("side_unknown")

    # Use max single hop as size proxy (paths hop through routers).
    usdc_amt: Decimal | None = None
    if usdc_transfers:
        usdc_amt = max(a for _f, _t, a in usdc_transfers)
    stock_amt: Decimal | None = None
    if stock_transfers:
        stock_amt = max(a for _t, _f, _to, a in stock_transfers)

    return DecodedRfqFill(
        tx_hash=tx_hash.lower(),
        maker=maker,
        taker=taker,
        pair_id=pair_id,
        token=token,
        usdc_amount=usdc_amt,
        stock_amount=stock_amt,
        maker_side=maker_side,
        external_addresses=tuple(sorted(external)),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Rebalance / CEX clustering
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransferEdge:
    ts_ms: int
    pair_id: str
    token: str
    frm: str
    to: str
    amount: Decimal
    tx_hash: str


@dataclass(frozen=True, slots=True)
class CexCluster:
    """High-degree counterparties hypothesized as exchange deposit/hot wallets."""

    address: str
    n_counterparties: int
    n_transfers: int
    volume: Decimal
    sample_counterparties: tuple[str, ...]


def cluster_cex_candidates(
    transfers: Sequence[TransferEdge],
    *,
    min_counterparties: int = 5,
    min_transfers: int = 8,
    exclude: Iterable[str] = (),
) -> list[CexCluster]:
    """Rank addresses that many EOAs send to / receive from (deposit-like).

    ``exclude`` should include pools, routers, wrappers, natives, zero address —
    they are high-degree by construction and are not CEX wallets.
    """
    excl = {a.lower() for a in exclude}
    zero = "0x" + "0" * 40
    excl.add(zero)
    # degree by unique counterparties (only count non-excluded nodes as candidates)
    cps: dict[str, set[str]] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)
    vol: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for t in transfers:
        a, b = t.frm.lower(), t.to.lower()
        if a == b:
            continue
        # Always record edges; later drop excluded candidates.
        if a not in excl:
            cps[a].add(b)
            counts[a] += 1
            vol[a] += t.amount
        if b not in excl:
            cps[b].add(a)
            counts[b] += 1
            vol[b] += t.amount
    out: list[CexCluster] = []
    for addr, partners in cps.items():
        # partner set should count only non-infra counterparties for "deposit-like"
        real_partners = {p for p in partners if p not in excl and p != zero}
        if len(real_partners) < min_counterparties or counts[addr] < min_transfers:
            continue
        sample = tuple(sorted(real_partners)[:8])
        out.append(
            CexCluster(
                address=addr,
                n_counterparties=len(real_partners),
                n_transfers=counts[addr],
                volume=vol[addr],
                sample_counterparties=sample,
            )
        )
    out.sort(key=lambda c: (-c.n_counterparties, -c.n_transfers, c.address))
    return out


def count_cex_touches(
    transfers: Sequence[TransferEdge],
    address: str,
    cex_wallets: Iterable[str],
    *,
    min_amount: Decimal = Decimal("0"),
) -> int:
    """Count transfers between ``address`` and any CEX-cluster wallet.

    ``min_amount`` filters dust (pass
    ``DraftThresholds.reb_min_transfer_notional_native`` for the draft gate).
    """
    addr = address.lower()
    cex = {c.lower() for c in cex_wallets}
    n = 0
    for t in transfers:
        if t.amount < min_amount:
            continue
        if t.frm.lower() == addr and t.to.lower() in cex:
            n += 1
        elif t.to.lower() == addr and t.frm.lower() in cex:
            n += 1
    return n
