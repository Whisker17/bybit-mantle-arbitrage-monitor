"""Per-block Mantle poller: pool state + swap/RFQ settlement logs."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from monitor.fluxion.abi import TOPIC0_ORDER_FILLED
from monitor.fluxion.events import decode_lop_fill_log, decode_v3_swap_log
from monitor.fluxion.pools import PoolMeta, fetch_pool_states
from monitor.fluxion.rpc import Rpc
from monitor.quotes import (
    CollectorGap,
    FluxionPoolStateTick,
    FluxionRfqFillTick,
    FluxionSwapTick,
)

logger = logging.getLogger(__name__)

# Soft cap of blocks processed per poll_once. Brief stalls catch up; larger
# lag jumps to the tip and records a gap (no historical backfill).
_MAX_CATCHUP_BLOCKS = 30


class ChainPoller:
    """Advance head block-by-block (no historical backfill)."""

    def __init__(
        self,
        rpc: Rpc,
        *,
        pools: list[PoolMeta],
        lop_address: str,
        head_lag_blocks: int = 2,
        max_block_gap: int = 1,
        fetch_swap_receipts: bool = True,
        max_catchup_blocks: int = _MAX_CATCHUP_BLOCKS,
        on_pool_state: Callable[[list[FluxionPoolStateTick]], None] | None = None,
        on_swaps: Callable[[list[FluxionSwapTick]], None] | None = None,
        on_rfq_fills: Callable[[list[FluxionRfqFillTick]], None] | None = None,
        on_gap: Callable[[CollectorGap], None] | None = None,
    ) -> None:
        self.rpc = rpc
        self.pools = list(pools)
        self.lop_address = lop_address
        self.head_lag_blocks = head_lag_blocks
        self.max_block_gap = max_block_gap
        self.fetch_swap_receipts = fetch_swap_receipts
        self.max_catchup_blocks = max_catchup_blocks
        self.on_pool_state = on_pool_state
        self.on_swaps = on_swaps
        self.on_rfq_fills = on_rfq_fills
        self.on_gap = on_gap
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

        # Any multi-block skip beyond max_block_gap flags subsequent rows as gap.
        if lag > self.max_block_gap:
            self._gap_pending = True

        # Far behind (disconnect / long stall): jump to a recent window and gap.
        if lag > self.max_catchup_blocks:
            now = int(time.time() * 1000)
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
            self._process_block(block, gap=self._gap_pending)
            self._last_block = block
            self._gap_pending = False
            handled += 1
        return handled

    def _process_block(self, block: int, *, gap: bool) -> None:
        recv = int(time.time() * 1000)
        blk = self.rpc.get_block(block, full_txs=False)
        if blk is None:
            logger.warning("block %s not found; marking gap", block)
            if self.on_gap:
                self.on_gap(
                    CollectorGap(
                        source="mantle_blocks",
                        gap_start_ms=recv,
                        gap_end_ms=recv,
                        detail=f"block {block} not found",
                    )
                )
            self._gap_pending = True
            return
        block_ts = int(blk["timestamp"], 16)

        if self.pools:
            states = fetch_pool_states(
                self.rpc,
                self.pools,
                block_number=block,
                block_ts=block_ts,
                recv_ts_ms=recv,
                gap=gap,
            )
            for s in states:
                self._token_order[s.pool.lower()] = (s.token0, s.token1)
            if states and self.on_pool_state:
                self.on_pool_state(states)

        pool_by_addr = {p.pool.lower(): p for p in self.pools}
        addresses = list(pool_by_addr.keys())
        swaps: list[FluxionSwapTick] = []
        if addresses:
            logs = self.rpc.get_logs(addresses, block, block)
            gas_by_tx: dict[str, int] = {}
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
                    if rcpt and "gasUsed" in rcpt:
                        gas_by_tx[txh] = int(rcpt["gasUsed"], 16)

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
                    recv_ts_ms=recv,
                    gas_used=gas_by_tx.get(txh),
                    gap=gap,
                )
                if tick is not None:
                    swaps.append(tick)
        if swaps and self.on_swaps:
            self.on_swaps(swaps)

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
            fill = decode_lop_fill_log(lg, block_ts=block_ts, recv_ts_ms=recv, gap=gap)
            if fill is not None:
                fills.append(fill)
        if fills and self.on_rfq_fills:
            self.on_rfq_fills(fills)

    def _resolve_tokens(self, meta: PoolMeta, block: int) -> tuple[str, str]:
        from monitor.fluxion.abi import SEL_TOKEN0, SEL_TOKEN1
        from monitor.fluxion.rpc import encode_call

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
