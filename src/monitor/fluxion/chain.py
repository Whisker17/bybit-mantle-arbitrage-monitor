"""Per-block Mantle poller: pool state + swap/RFQ settlement + ERC-20 transfers."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from monitor.fluxion.abi import (
    NATIVE_DECIMALS_DEFAULT,
    SEL_TOKEN0,
    SEL_TOKEN1,
    TOPIC0_ORDER_FILLED,
    TOPIC0_TRANSFER,
)
from monitor.fluxion.events import (
    decode_erc20_transfer_log,
    decode_lop_fill_log,
    decode_v3_swap_log,
)
from monitor.fluxion.pools import PoolMeta, fetch_pool_states
from monitor.fluxion.rfq_decode import (
    apply_decoded_rfq_enrichment,
    decode_rfq_fill_from_receipt,
)
from monitor.fluxion.rpc import Rpc, encode_call
from monitor.quotes import (
    CollectorGap,
    Erc20TransferTick,
    FluxionPoolStateTick,
    FluxionRfqFillTick,
    FluxionSwapTick,
    now_ms,
)

logger = logging.getLogger(__name__)

_MAX_CATCHUP_BLOCKS = 15


class ChainPoller:
    """Advance head block-by-block (no historical backfill)."""

    def __init__(
        self,
        rpc: Rpc,
        *,
        pools: list[PoolMeta],
        lop_address: str,
        head_lag_blocks: int = 0,
        max_block_gap: int = 1,
        fetch_swap_receipts: bool = True,
        max_catchup_blocks: int = _MAX_CATCHUP_BLOCKS,
        # WHI-768: token→pair map for RFQ enrichment + native Transfer stream.
        token_to_pair: Mapping[str, str] | None = None,
        usdc: str | None = None,
        transfer_tokens: Mapping[str, str] | None = None,
        # token_addr → decimals for Transfer amount decode (default 18).
        transfer_decimals: Mapping[str, int] | None = None,
        enrich_rfq_fills: bool = True,
        on_pool_state: Callable[[list[FluxionPoolStateTick]], None] | None = None,
        on_swaps: Callable[[list[FluxionSwapTick]], None] | None = None,
        on_rfq_fills: Callable[[list[FluxionRfqFillTick]], None] | None = None,
        on_transfers: Callable[[list[Erc20TransferTick]], None] | None = None,
        on_gap: Callable[[CollectorGap], None] | None = None,
        # Optional: (block_number, block_ts, discovered_ms, recv_ts_ms) after success.
        # discovered_ms is wall clock just before getBlock; recv after all RPC.
        on_block_done: Callable[[int, int, int, int], None] | None = None,
    ) -> None:
        self.rpc = rpc
        self.pools = list(pools)
        self.lop_address = lop_address
        self.head_lag_blocks = head_lag_blocks
        self.max_block_gap = max_block_gap
        self.fetch_swap_receipts = fetch_swap_receipts
        self.max_catchup_blocks = max_catchup_blocks
        self.token_to_pair = {
            k.lower(): v for k, v in (token_to_pair or {}).items()
        }
        self.usdc = (usdc or "").lower() or None
        self.transfer_tokens = {
            k.lower(): v for k, v in (transfer_tokens or {}).items()
        }
        self.transfer_decimals = {
            k.lower(): int(v) for k, v in (transfer_decimals or {}).items()
        }
        self.enrich_rfq_fills = enrich_rfq_fills
        self.on_pool_state = on_pool_state
        self.on_swaps = on_swaps
        self.on_rfq_fills = on_rfq_fills
        self.on_transfers = on_transfers
        self.on_gap = on_gap
        self.on_block_done = on_block_done
        self._last_block: int | None = None
        self._token_order: dict[str, tuple[str, str]] = {}
        self._gap_pending = False

    @property
    def last_block(self) -> int | None:
        return self._last_block

    def poll_once(self) -> int:
        """Process new blocks up to settled head. Returns number of blocks handled."""
        head = self.rpc.block_number() - self.head_lag_blocks
        if head < 0:
            return 0
        if self._last_block is None:
            # Start from current head only — pure realtime, no backfill.
            self._last_block = head - 1

        lag = head - self._last_block
        if lag <= 0:
            return 0

        if lag > self.max_block_gap:
            self._gap_pending = True

        if lag > self.max_catchup_blocks:
            now = now_ms()
            if self.on_gap:
                self.on_gap(
                    CollectorGap(
                        source="mantle_blocks",
                        gap_start_ms=now,
                        gap_end_ms=now,
                        detail=(
                            f"lag={lag} last={self._last_block} head={head}; "
                            f"skipping to tip (no historical backfill)"
                        ),
                    )
                )
            self._last_block = head - self.max_catchup_blocks
            self._gap_pending = True

        start = self._last_block + 1
        end = head
        handled = 0
        for block in range(start, end + 1):
            ok = self._process_block(block, gap=self._gap_pending)
            if ok:
                self._last_block = block
                # Only clear gap after a successfully processed block.
                self._gap_pending = False
                handled += 1
            else:
                # Missing block: do not advance past it; retry next poll.
                break
        return handled

    def _process_block(self, block: int, *, gap: bool) -> bool:
        """Return True if the block was fully handled.

        ``recv_ts_ms`` is stamped **after** RPC work so block_ts→DB latency
        matches the WHI-731 acceptance metric (not poll-start optimism).
        """
        miss_ts = now_ms()
        blk = self.rpc.get_block(block, full_txs=False)
        if blk is None:
            logger.warning("block %s not found; marking gap", block)
            if self.on_gap:
                self.on_gap(
                    CollectorGap(
                        source="mantle_blocks",
                        gap_start_ms=miss_ts,
                        gap_end_ms=now_ms(),
                        detail=f"block {block} not found",
                    )
                )
            self._gap_pending = True
            return False
        block_ts = int(blk["timestamp"], 16)

        states: list[FluxionPoolStateTick] = []
        if self.pools:
            # Temporary recv; rewritten after all RPC for this block completes.
            states = fetch_pool_states(
                self.rpc,
                self.pools,
                block_number=block,
                block_ts=block_ts,
                recv_ts_ms=0,
                gap=gap,
            )
            for s in states:
                self._token_order[s.pool.lower()] = (s.token0, s.token1)
            if self.pools and not states:
                logger.warning(
                    "block %s: all %d pool state decodes failed", block, len(self.pools)
                )

        pool_by_addr = {p.pool.lower(): p for p in self.pools}
        addresses = list(pool_by_addr.keys())
        swaps: list[FluxionSwapTick] = []
        gas_used_by_tx: dict[str, int] = {}
        gas_price_by_tx: dict[str, int] = {}
        if addresses:
            logs = self.rpc.get_logs(addresses, block, block)
            if self.fetch_swap_receipts:
                tx_hashes = {
                    str(lg.get("transactionHash") or "").lower()
                    for lg in logs
                    if lg.get("transactionHash")
                }
                for txh in tx_hashes:
                    if not txh:
                        continue
                    try:
                        rcpt = self.rpc.get_transaction_receipt(txh)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("receipt %s failed: %s", txh, exc)
                        continue
                    used, price = _gas_from_receipt(rcpt)
                    if used is not None:
                        gas_used_by_tx[txh] = used
                    if price is not None:
                        gas_price_by_tx[txh] = price

            for lg in logs:
                addr = str(lg.get("address") or "").lower()
                meta = pool_by_addr.get(addr)
                if meta is None:
                    continue
                if addr in self._token_order:
                    token0, token1 = self._token_order[addr]
                else:
                    token0, token1 = self._resolve_tokens(meta, block)
                txh = str(lg.get("transactionHash") or "").lower()
                tick = decode_v3_swap_log(
                    lg,
                    meta=meta,
                    token0=token0,
                    token1=token1,
                    block_ts=block_ts,
                    recv_ts_ms=0,
                    gas_used=gas_used_by_tx.get(txh),
                    effective_gas_price=gas_price_by_tx.get(txh),
                    gap=gap,
                )
                if tick is not None:
                    swaps.append(tick)

        fills: list[FluxionRfqFillTick] = []
        try:
            lop_logs = self.rpc.get_logs(
                [self.lop_address],
                block,
                block,
                topics=[[TOPIC0_ORDER_FILLED]],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("LOP getLogs failed at block %s: %s", block, exc)
            lop_logs = []
        for lg in lop_logs:
            fill = decode_lop_fill_log(lg, block_ts=block_ts, recv_ts_ms=0, gap=gap)
            if fill is not None:
                fills.append(fill)

        # WHI-768: receipt-enrich RFQ fills (volume is tiny; one receipt per fill).
        if fills and self.enrich_rfq_fills and self.usdc:
            fills = [self._enrich_rfq_fill(f) for f in fills]

        # WHI-768: native xStock ERC-20 Transfer stream (prefer native inventory asset).
        transfers: list[Erc20TransferTick] = []
        if self.transfer_tokens:
            try:
                xfer_logs = self.rpc.get_logs(
                    list(self.transfer_tokens.keys()),
                    block,
                    block,
                    topics=[[TOPIC0_TRANSFER]],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Transfer getLogs failed at block %s: %s", block, exc)
                xfer_logs = []
            for lg in xfer_logs:
                token = str(lg.get("address") or "").lower()
                pair_id = self.transfer_tokens.get(token)
                if pair_id is None:
                    continue
                dec = self.transfer_decimals.get(token, NATIVE_DECIMALS_DEFAULT)
                tick = decode_erc20_transfer_log(
                    lg,
                    pair_id=pair_id,
                    token=token,
                    block_ts=block_ts,
                    recv_ts_ms=0,
                    decimals=dec,
                    gap=gap,
                )
                if tick is not None:
                    transfers.append(tick)

        # Stamp after all RPC for this block — matches block_ts → DB latency AC.
        recv = now_ms()
        if states and self.on_pool_state:
            self.on_pool_state([replace(s, recv_ts_ms=recv) for s in states])
        if swaps and self.on_swaps:
            self.on_swaps([replace(t, recv_ts_ms=recv) for t in swaps])
        if fills and self.on_rfq_fills:
            self.on_rfq_fills([replace(f, recv_ts_ms=recv) for f in fills])
        if transfers and self.on_transfers:
            self.on_transfers([replace(t, recv_ts_ms=recv) for t in transfers])
        if self.on_block_done:
            # miss_ts = discovery wall clock (pre-getBlock); see latency probe WHI-749.
            self.on_block_done(block, block_ts, miss_ts, recv)
        return True

    def _enrich_rfq_fill(self, fill: FluxionRfqFillTick) -> FluxionRfqFillTick:
        """Best-effort maker/taker/pair enrichment from the fill receipt."""
        txh = fill.tx_hash.lower()
        if not txh or not self.usdc:
            return fill
        try:
            rcpt = self.rpc.get_transaction_receipt(txh)
        except Exception as exc:  # noqa: BLE001
            logger.debug("RFQ receipt %s failed: %s", txh, exc)
            return fill
        if not rcpt:
            return fill
        logs = rcpt.get("logs") or []
        if not isinstance(logs, list):
            return fill
        decoded = decode_rfq_fill_from_receipt(
            tx_hash=txh,
            logs=logs,  # type: ignore[arg-type]
            usdc=self.usdc,
            lop=self.lop_address,
            settlement_router=None,
            token_to_pair=self.token_to_pair,
        )
        return apply_decoded_rfq_enrichment(fill, decoded, usdc=self.usdc)

    def _resolve_tokens(self, meta: PoolMeta, block: int) -> tuple[str, str]:
        try:
            t0 = self.rpc.eth_call(
                meta.pool, "0x" + encode_call(SEL_TOKEN0).hex(), block
            )
            t1 = self.rpc.eth_call(
                meta.pool, "0x" + encode_call(SEL_TOKEN1).hex(), block
            )
            token0 = "0x" + bytes.fromhex(t0[2:])[-20:].hex()
            token1 = "0x" + bytes.fromhex(t1[2:])[-20:].hex()
            self._token_order[meta.pool.lower()] = (token0, token1)
            return token0, token1
        except Exception:  # noqa: BLE001
            return meta.wrapper_token.lower(), meta.quote_token.lower()


def _gas_from_receipt(rcpt: dict[str, Any] | None) -> tuple[int | None, int | None]:
    if not rcpt:
        return None, None
    used: int | None = None
    price: int | None = None
    if "gasUsed" in rcpt:
        raw = rcpt["gasUsed"]
        used = int(raw, 16) if isinstance(raw, str) else int(raw)
    egp = rcpt.get("effectiveGasPrice") or rcpt.get("gasPrice")
    if egp is not None:
        price = int(egp, 16) if isinstance(egp, str) else int(egp)
    return used, price
