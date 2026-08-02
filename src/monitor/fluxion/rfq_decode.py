"""RFQ LOP fill receipt decode + enrichment (WHI-768).

Pure over already-fetched logs — used by the collector (``fluxion.chain``) and
the one-shot backfill CLI. Attribution research (``mm_draft``) re-exports the
same symbols so the WHI-767 analysis script keeps working.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from eth_utils import keccak  # type: ignore[attr-defined]

from monitor.quotes import FluxionRfqFillTick

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




def apply_decoded_rfq_enrichment(
    base: FluxionRfqFillTick,
    decoded: DecodedRfqFill,
    *,
    usdc: str,
    mark_attempted: bool = True,
) -> FluxionRfqFillTick:
    """Map a ``DecodedRfqFill`` onto tick enrichment fields (shared by live + backfill).

    ``mark_attempted=True`` sets ``enriched`` after a successful receipt decode
    attempt even when maker/pair stay null, so backfill does not re-fetch forever.
    """
    usdc_l = usdc.lower()
    making_amt = (
        str(decoded.stock_amount)
        if decoded.maker_side == "sell_native" and decoded.stock_amount is not None
        else (
            str(decoded.usdc_amount)
            if decoded.maker_side == "buy_native" and decoded.usdc_amount is not None
            else None
        )
    )
    taking_amt = (
        str(decoded.usdc_amount)
        if decoded.maker_side == "sell_native" and decoded.usdc_amount is not None
        else (
            str(decoded.stock_amount)
            if decoded.maker_side == "buy_native" and decoded.stock_amount is not None
            else None
        )
    )
    making_token = decoded.token if decoded.maker_side == "sell_native" else usdc_l
    taking_token = usdc_l if decoded.maker_side == "sell_native" else decoded.token
    resolved = bool(decoded.maker is not None or decoded.pair_id is not None)
    return replace(
        base,
        pair_id=decoded.pair_id,
        maker=decoded.maker,
        taker=decoded.taker,
        direction=decoded.maker_side,
        making_token=making_token,
        taking_token=taking_token,
        making_amount=making_amt,
        taking_amount=taking_amt,
        usdc_amount=(
            None if decoded.usdc_amount is None else str(decoded.usdc_amount)
        ),
        stock_amount=(
            None if decoded.stock_amount is None else str(decoded.stock_amount)
        ),
        # Attempted receipt decode → do not re-queue; partial fills still enriched.
        enriched=mark_attempted or resolved,
    )
