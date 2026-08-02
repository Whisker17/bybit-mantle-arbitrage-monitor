"""Orchestrate CEX WS + chain poll (+ RFQ) → per-market SQLite.

Supports:

* ``bybit-fluxion`` — Bybit WS + Mantle Fluxion + RFQ (M2)
* ``binance-pancake`` — Binance WS + BSC Pancake V3, no RFQ (M7-3 / WHI-772)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from monitor.binance.ws import BinanceWsCollector
from monitor.bybit.ws import BybitWsCollector
from monitor.cex_volume.poller import CexVolumePoller
from monitor.collector.config import (
    CollectorConfig,
    load_collector_config,
    load_dotenv,
    resolve_bsc_rpc_url,
    resolve_mantle_rpc_url,
    rpc_url_kind,
)
from monitor.collector.latency import LatencyTracker, block_ingest_latency_ms
from monitor.fluxion.chain import ChainPoller
from monitor.fluxion.pools import PoolMeta
from monitor.fluxion.rfq import RfqPoller
from monitor.fluxion.rpc import Rpc
from monitor.markets import DEFAULT_MARKET_ID, load_market_context, resolve_market_sqlite
from monitor.quotes import (
    BybitBookTick,
    BybitDepthTick,
    BybitTradeTick,
    CexVolumeTick,
    CollectorGap,
    Erc20TransferTick,
    FluxionPoolStateTick,
    FluxionRfqFillTick,
    FluxionSwapTick,
    now_ms,
)
from monitor.storage import SqliteStore
from monitor.symbols import load_pairs_config
from monitor.symbols.bstocks_load import load_bstocks_pairs_config
from monitor.symbols.bstocks_models import BStocksPairsConfig
from monitor.symbols.models import PairsConfig
from monitor.symbols.multipliers import multiplier_map, ui_multiplier_map
from monitor.symbols.token_map import (
    inventory_token_to_pair,
    native_token_decimals,
    native_token_to_pair,
)
from monitor.underlying.config import UnderlyingConfigError, load_underlying_config
from monitor.underlying.poller import UnderlyingPoller
from monitor.underlying.tickers import underlying_tickers_for_pairs

logger = logging.getLogger(__name__)


class CollectorDaemon:
    """Long-running realtime collector (no historical backfill)."""

    def __init__(
        self,
        pairs: PairsConfig | None,
        collector: CollectorConfig,
        store: SqliteStore,
        *,
        bstocks: BStocksPairsConfig | None = None,
        rpc_url: str | None = None,
        market_id: str = DEFAULT_MARKET_ID,
    ) -> None:
        if collector.is_bybit_fluxion and pairs is None:
            raise ValueError("bybit-fluxion collector requires pairs inventory")
        if collector.is_binance_pancake and bstocks is None:
            raise ValueError("binance-pancake collector requires bstocks inventory")
        self.pairs = pairs
        self.bstocks = bstocks
        self.cfg = collector
        self.store = store
        self.market_id = market_id
        if collector.is_binance_pancake:
            if collector.bsc is None:
                raise ValueError("binance-pancake collector requires bsc config")
            self.rpc_url = rpc_url or resolve_bsc_rpc_url(collector.bsc.public_rpc_url)
        else:
            if collector.mantle is None:
                raise ValueError("bybit-fluxion collector requires mantle config")
            self.rpc_url = rpc_url or resolve_mantle_rpc_url(collector.mantle.public_rpc_url)
        self._stop = asyncio.Event()
        self._rfq_gap = False
        # Set by retention loop under disk-critical waterline (WHI-751).
        self._book_writes_paused = False
        self._book_pause_logged = False
        self._book_pause_started_ms: int | None = None
        # After resume, mark all pairs' books gap=1 until this wall-clock ms
        # (mirrors WS collector post-reconnect gap window).
        self._book_gap_until_ms: int = 0
        # Last written L1 row fingerprint — skip duplicate rows under hot books.
        self._last_book_l1: dict[str, tuple[Decimal, Decimal, bool]] = {}
        self._cex_volume_poller: CexVolumePoller | None = None

    def request_stop(self) -> None:
        self._stop.set()

    def _stamp_collector_meta(self) -> None:
        """Wall-clock start + write-once first-ever start (WHI-777 truncation).

        ``collector_started_ms`` is this process (rewritten every boot).
        ``collector_first_started_ms`` is permanent so volume windows still
        look like a full 24h after a routine restart when swaps are retained.
        """
        ts = now_ms()
        self.store.set_meta("collector_started_ms", str(ts))
        self.store.set_meta("market_id", self.market_id)
        self.store.set_meta("rpc_url_kind", rpc_url_kind(self.rpc_url))
        if self.store.get_meta("collector_first_started_ms") is None:
            self.store.set_meta("collector_first_started_ms", str(ts))

    async def run(self) -> None:
        if self.cfg.is_binance_pancake:
            await self._run_binance_pancake()
        else:
            await self._run_bybit_fluxion()

    async def _run_bybit_fluxion(self) -> None:
        if self.pairs is None or self.cfg.bybit is None:
            raise RuntimeError("bybit-fluxion run requires pairs + bybit config")
        pair_id_by_symbol = {p.bybit.symbol.upper(): p.id for p in self.pairs.pairs}
        mult_by_symbol = {
            k.upper(): v for k, v in multiplier_map(self.pairs).items()
        }
        symbols = list(pair_id_by_symbol.keys())
        depth_cfg = self.cfg.bybit.depth

        bybit = BybitWsCollector(
            ws_url=self.cfg.bybit.ws_url,
            symbols=symbols,
            pair_id_by_symbol=pair_id_by_symbol,
            multiplier_by_symbol=mult_by_symbol,
            on_book=self._on_book,
            on_trade=self._on_trade,
            on_gap=self._on_gap,
            on_depth=self._on_depth if depth_cfg.enabled else None,
            book_topic_prefix=self.cfg.bybit.book_topic_prefix,
            trade_topic_prefix=self.cfg.bybit.trade_topic_prefix,
            reconnect_min_s=self.cfg.bybit.reconnect_min_s,
            reconnect_max_s=self.cfg.bybit.reconnect_max_s,
            post_reconnect_gap_s=self.cfg.bybit.post_reconnect_gap_s,
            ping_interval_s=self.cfg.bybit.ping_interval_s,
            depth_enabled=depth_cfg.enabled,
            depth_buckets_usd=depth_cfg.buckets_usd,
            depth_emit_interval_ms=depth_cfg.emit_interval_ms,
            depth_mid_change_bps=depth_cfg.mid_change_bps,
        )

        tasks = [
            asyncio.create_task(bybit.run(), name="bybit_ws"),
            asyncio.create_task(self._chain_loop_mantle(), name="mantle_chain"),
            asyncio.create_task(self._rfq_loop(), name="rfq_poll"),
            asyncio.create_task(self._retention_loop(), name="retention"),
            asyncio.create_task(
                self._attribution_refresh_loop(), name="attribution_refresh"
            ),
            asyncio.create_task(self._underlying_loop(), name="underlying"),
        ]
        vol_task = self._maybe_cex_volume_task(pair_id_by_symbol)
        if vol_task is not None:
            tasks.append(vol_task)
        self._stamp_collector_meta()
        logger.info(
            "collector started market=%s pairs=%d amm_pools=%d sqlite=%s rpc_kind=%s "
            "retention=%s bybit_book=%s depth=%s cex_volume=%s",
            self.market_id,
            len(self.pairs.pairs),
            len(self.pairs.pairs_with_amm()),
            self.store.path,
            rpc_url_kind(self.rpc_url),
            self.cfg.retention.enabled,
            self.cfg.bybit.book_topic_prefix,
            depth_cfg.enabled,
            self.cfg.cex_volume is not None and self.cfg.cex_volume.enabled,
        )
        try:
            await self._stop.wait()
        finally:
            bybit.request_stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._finalize_stop("bybit_book")

    async def _run_binance_pancake(self) -> None:
        if self.bstocks is None or self.cfg.binance is None or self.cfg.bsc is None:
            raise RuntimeError(
                "binance-pancake run requires bstocks inventory + binance/bsc config"
            )
        pair_id_by_symbol = {
            p.binance.symbol.upper(): p.id for p in self.bstocks.pairs
        }
        mult_by_symbol = ui_multiplier_map(self.bstocks)
        symbols = list(pair_id_by_symbol.keys())
        depth_cfg = self.cfg.binance.depth

        binance = BinanceWsCollector(
            ws_base_url=self.cfg.binance.ws_base_url,
            symbols=symbols,
            pair_id_by_symbol=pair_id_by_symbol,
            ui_multiplier_by_symbol=mult_by_symbol,
            on_book=self._on_book,
            on_trade=self._on_trade,
            on_gap=self._on_gap,
            on_depth=self._on_depth if depth_cfg.enabled else None,
            book_stream=self.cfg.binance.book_stream,
            depth_stream=depth_cfg.stream,
            trade_stream=self.cfg.binance.trade_stream,
            reconnect_min_s=self.cfg.binance.reconnect_min_s,
            reconnect_max_s=self.cfg.binance.reconnect_max_s,
            post_reconnect_gap_s=self.cfg.binance.post_reconnect_gap_s,
            ping_interval_s=self.cfg.binance.ping_interval_s,
            depth_enabled=depth_cfg.enabled,
            depth_buckets_usd=depth_cfg.buckets_usd,
            depth_emit_interval_ms=depth_cfg.emit_interval_ms,
            depth_mid_change_bps=depth_cfg.mid_change_bps,
        )

        tasks = [
            asyncio.create_task(binance.run(), name="binance_ws"),
            asyncio.create_task(self._chain_loop_bsc(), name="bsc_chain"),
            asyncio.create_task(self._retention_loop(), name="retention"),
            asyncio.create_task(self._underlying_loop(), name="underlying"),
            # No RFQ / MM attribution refresh for AMM-only pancake market.
        ]
        vol_task = self._maybe_cex_volume_task(pair_id_by_symbol)
        if vol_task is not None:
            tasks.append(vol_task)
        self._stamp_collector_meta()
        logger.info(
            "collector started market=%s pairs=%d amm_pools=%d sqlite=%s rpc_kind=%s "
            "retention=%s binance_ws=%s depth=%s pool_stride=%d cex_volume=%s",
            self.market_id,
            len(self.bstocks.pairs),
            len(self.bstocks.pairs_with_amm()),
            self.store.path,
            rpc_url_kind(self.rpc_url),
            self.cfg.retention.enabled,
            self.cfg.binance.ws_base_url,
            depth_cfg.enabled,
            self.cfg.bsc.pool_state_every_n_blocks,
            self.cfg.cex_volume is not None and self.cfg.cex_volume.enabled,
        )
        try:
            await self._stop.wait()
        finally:
            binance.request_stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._finalize_stop("cex_book")

    def _finalize_stop(self, book_label: str) -> None:
        if self._cex_volume_poller is not None:
            self._cex_volume_poller.request_stop()
        if self._book_writes_paused and self._book_pause_started_ms is not None:
            end = now_ms()
            self.store.insert_gap(
                CollectorGap(
                    source="disk_critical",
                    gap_start_ms=self._book_pause_started_ms,
                    gap_end_ms=end,
                    detail=f"collector stopped while {book_label} writes paused",
                )
            )
        self.store.set_meta("collector_stopped_ms", str(now_ms()))
        logger.info("collector stopped market=%s", self.market_id)

    async def _on_book(self, tick: BybitBookTick) -> None:
        if self._book_writes_paused:
            if not self._book_pause_logged:
                logger.error(
                    "skip book writes: disk critical (retention pause active)"
                )
                self._book_pause_logged = True
            return
        if tick.recv_ts_ms < self._book_gap_until_ms or (
            not tick.gap and now_ms() < self._book_gap_until_ms
        ):
            tick = replace(tick, gap=True)
        key = (tick.bid, tick.ask, tick.gap)
        if self._last_book_l1.get(tick.pair_id) == key:
            return
        self._last_book_l1[tick.pair_id] = key
        await asyncio.to_thread(self.store.insert_bybit_book, [tick])

    async def _on_depth(self, tick: BybitDepthTick) -> None:
        """Persist a precomputed VWAP curve (already throttled in the WS collector)."""
        if self._book_writes_paused:
            return
        if tick.recv_ts_ms < self._book_gap_until_ms or (
            not tick.gap and now_ms() < self._book_gap_until_ms
        ):
            tick = replace(tick, gap=True)
        await asyncio.to_thread(self.store.insert_bybit_depth, [tick])

    async def _on_trade(self, tick: BybitTradeTick) -> None:
        await asyncio.to_thread(self.store.insert_bybit_trades, [tick])

    def _maybe_cex_volume_task(
        self, pair_id_by_symbol: dict[str, str]
    ) -> asyncio.Task[None] | None:
        """Start REST 24h volume poll when configured and enabled (WHI-777)."""
        cfg = self.cfg.cex_volume
        if cfg is None or not cfg.enabled:
            return None
        poller = CexVolumePoller(
            venue=cfg.venue,
            rest_base_url=cfg.rest_base_url,
            pair_id_by_symbol=pair_id_by_symbol,
            on_volume=self._on_cex_volume,
            poll_interval_s=cfg.poll_interval_s,
            http_timeout_s=cfg.http_timeout_s,
        )
        self._cex_volume_poller = poller
        return asyncio.create_task(poller.run(), name="cex_volume")

    async def _on_cex_volume(self, ticks: list[CexVolumeTick]) -> None:
        await asyncio.to_thread(self.store.insert_cex_volume, ticks)

    async def _on_gap(self, gap: CollectorGap) -> None:
        await asyncio.to_thread(self.store.insert_gap, gap)
        logger.warning(
            "gap source=%s detail=%s duration_ms=%s",
            gap.source,
            gap.detail,
            gap.gap_end_ms - gap.gap_start_ms,
        )

    async def _chain_loop_mantle(self) -> None:
        if self.pairs is None or self.cfg.mantle is None:
            raise RuntimeError("mantle chain loop requires pairs + mantle config")
        pools = [
            PoolMeta(
                pair_id=p.id,
                pool=p.fluxion.amm.pool,
                wrapper_token=p.fluxion.wrapper_token,
                native_token=p.fluxion.native_token,
                quote_token=p.fluxion.quote_token_address,
                native_decimals=p.fluxion.native_decimals,
            )
            for p in self.pairs.pairs_with_amm()
            if p.fluxion.amm is not None
        ]
        rpc = Rpc(
            self.rpc_url,
            multicall3=self.cfg.mantle.multicall3,
            min_interval=self.cfg.mantle.rpc_min_interval_s,
            timeout=self.cfg.mantle.rpc_timeout_s,
            retries=self.cfg.mantle.rpc_retries,
        )
        latency_tracker = LatencyTracker(window=self.cfg.mantle.latency_window_blocks)
        transfer_map = (
            native_token_to_pair(self.pairs)
            if self.cfg.mantle.collect_erc20_transfers
            else {}
        )
        poller = ChainPoller(
            rpc,
            pools=pools,
            lop_address=self.pairs.contracts.limit_order_protocol,
            head_lag_blocks=self.cfg.mantle.head_lag_blocks,
            max_block_gap=self.cfg.mantle.max_block_gap,
            max_catchup_blocks=self.cfg.mantle.max_catchup_blocks,
            fetch_swap_receipts=self.cfg.mantle.fetch_swap_receipts,
            token_to_pair=inventory_token_to_pair(self.pairs),
            usdc=self.pairs.contracts.usdc,
            transfer_tokens=transfer_map,
            transfer_decimals=native_token_decimals(self.pairs),
            enrich_rfq_fills=self.cfg.mantle.enrich_rfq_fills,
            gap_source="mantle_blocks",
            on_pool_state=self._on_pool_state,
            on_swaps=self._on_swaps,
            on_rfq_fills=self._on_rfq_fills,
            on_transfers=self._on_transfers if transfer_map else None,
            on_gap=self._on_chain_gap,
            on_block_done=self._make_on_block_done(latency_tracker),
        )
        await self._poll_chain(
            poller,
            rpc,
            interval_s=self.cfg.mantle.block_poll_interval_s,
            gap_source="mantle_blocks",
        )

    async def _chain_loop_bsc(self) -> None:
        if self.bstocks is None or self.cfg.bsc is None:
            raise RuntimeError("bsc chain loop requires bstocks + bsc config")
        quote_dec = self.cfg.bsc.quote_decimals
        pools = [
            PoolMeta(
                pair_id=p.id,
                pool=p.pancake.amm.pool,
                # No ERC-4626 wrapper — native trades raw in the V3 pool.
                wrapper_token=p.pancake.native_token,
                native_token=p.pancake.native_token,
                quote_token=p.pancake.quote_token_address,
                native_decimals=p.pancake.native_decimals,
                wrapper_decimals=p.pancake.native_decimals,
                quote_decimals=quote_dec,
                has_erc4626_wrapper=False,
            )
            for p in self.bstocks.pairs_with_amm()
            if p.pancake.amm is not None
        ]
        rpc = Rpc(
            self.rpc_url,
            multicall3=self.cfg.bsc.multicall3,
            min_interval=self.cfg.bsc.rpc_min_interval_s,
            timeout=self.cfg.bsc.rpc_timeout_s,
            retries=self.cfg.bsc.rpc_retries,
        )
        latency_tracker = LatencyTracker(window=self.cfg.bsc.latency_window_blocks)
        poller = ChainPoller(
            rpc,
            pools=pools,
            lop_address="",  # AMM-only — no RFQ / LOP
            head_lag_blocks=self.cfg.bsc.head_lag_blocks,
            max_block_gap=self.cfg.bsc.max_block_gap,
            max_catchup_blocks=self.cfg.bsc.max_catchup_blocks,
            fetch_swap_receipts=self.cfg.bsc.fetch_swap_receipts,
            enrich_rfq_fills=False,
            gap_source="bsc_blocks",
            pool_state_every_n_blocks=self.cfg.bsc.pool_state_every_n_blocks,
            on_pool_state=self._on_pool_state,
            on_swaps=self._on_swaps,
            on_gap=self._on_chain_gap,
            on_block_done=self._make_on_block_done(latency_tracker),
        )
        await self._poll_chain(
            poller,
            rpc,
            interval_s=self.cfg.bsc.block_poll_interval_s,
            gap_source="bsc_blocks",
        )

    def _on_pool_state(self, ticks: list[FluxionPoolStateTick]) -> None:
        self.store.insert_pool_state(ticks)

    def _on_swaps(self, ticks: list[FluxionSwapTick]) -> None:
        self.store.insert_swaps(ticks)

    def _on_rfq_fills(self, ticks: list[FluxionRfqFillTick]) -> None:
        self.store.insert_rfq_fills(ticks)

    def _on_transfers(self, ticks: list[Erc20TransferTick]) -> None:
        self.store.insert_erc20_transfers(ticks)

    def _on_chain_gap(self, gap: CollectorGap) -> None:
        self.store.insert_gap(gap)
        logger.warning("chain gap: %s", gap.detail)

    def _make_on_block_done(
        self, latency_tracker: LatencyTracker
    ) -> Callable[[int, int, int, int], None]:
        def on_block_done(
            block_number: int, block_ts: int, _discovered_ms: int, recv_ts_ms: int
        ) -> None:
            latency_ms = block_ingest_latency_ms(block_ts, recv_ts_ms)
            report = latency_tracker.add(latency_ms)
            self.store.set_meta("last_block_ingest_latency_ms", str(latency_ms))
            self.store.set_meta("last_block", str(block_number))
            self.store.set_meta(
                "block_ingest_latency_p50_ms", str(int(report.p50 or 0))
            )
            self.store.set_meta(
                "block_ingest_latency_p95_ms", str(int(report.p95 or 0))
            )
            self.store.set_meta(
                "block_ingest_latency_p99_ms", str(int(report.p99 or 0))
            )
            self.store.set_meta("block_ingest_latency_n", str(report.count))

        return on_block_done

    async def _poll_chain(
        self,
        poller: ChainPoller,
        rpc: Rpc,
        *,
        interval_s: float,
        gap_source: str,
    ) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.to_thread(poller.poll_once)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("chain poll error: %s", exc)
                    await asyncio.to_thread(
                        self.store.insert_gap,
                        CollectorGap(
                            source=gap_source,
                            gap_start_ms=now_ms(),
                            gap_end_ms=now_ms(),
                            detail=f"poll error: {exc}",
                        ),
                    )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval_s)
                except TimeoutError:
                    pass
        finally:
            rpc.close()

    async def _rfq_loop(self) -> None:
        if self.pairs is None or self.cfg.rfq is None:
            raise RuntimeError("rfq loop requires pairs + rfq config")
        poller = RfqPoller(
            pairs=list(self.pairs.pairs),
            rfq=self.pairs.rfq,
            amount_usdc_raw=self.cfg.rfq.amount_usdc_raw,
            amount_native_raw=self.cfg.rfq.amount_native_raw,
            prefer_primary_url=self.cfg.rfq.prefer_primary_url,
            poll_both_sides=self.cfg.rfq.poll_both_sides,
            http_timeout_s=self.cfg.rfq.http_timeout_s,
        )
        interval = poller.poll_interval_s()
        try:
            while not self._stop.is_set():
                try:
                    tick = await asyncio.to_thread(poller.poll_next, gap=self._rfq_gap)
                    self._rfq_gap = False
                    await asyncio.to_thread(self.store.insert_rfq_quotes, [tick])
                except Exception as exc:  # noqa: BLE001
                    logger.exception("rfq poll error: %s", exc)
                    self._rfq_gap = True
                    await asyncio.to_thread(
                        self.store.insert_gap,
                        CollectorGap(
                            source="rfq_poll",
                            gap_start_ms=now_ms(),
                            gap_end_ms=now_ms(),
                            detail=f"poll error: {exc}",
                        ),
                    )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                except TimeoutError:
                    pass
        finally:
            poller.close()

    async def _attribution_refresh_loop(self) -> None:
        """Periodic address_labels + rebalance_events refresh (WHI-768)."""
        interval = self.cfg.attribution_refresh_interval_s
        if interval <= 0:
            logger.info("attribution refresh disabled (interval_s=0)")
            return
        first = True
        while not self._stop.is_set():
            delay = 30.0 if first else interval
            first = False
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                break
            except TimeoutError:
                pass
            if self._stop.is_set():
                break
            try:
                from monitor.attribution.refresh import run_refresh

                stats = await asyncio.to_thread(run_refresh, self.store)
                logger.info("attribution refresh: %s", stats)
            except Exception as exc:  # noqa: BLE001
                logger.exception("attribution refresh error: %s", exc)

    def _underlying_tickers_for_market(self) -> list[str]:
        """Canonical underlyings present in this market's inventory."""
        if self.pairs is not None:
            return underlying_tickers_for_pairs([p.id for p in self.pairs.pairs])
        if self.bstocks is not None:
            return underlying_tickers_for_pairs([p.id for p in self.bstocks.pairs])
        return []

    async def _underlying_loop(self) -> None:
        """Poll Pyth Hermes (+ optional Yahoo) into underlying_prices (WHI-778)."""
        if not self.cfg.underlying_enabled:
            logger.info("underlying poller disabled (collector.yaml underlying.enabled)")
            return
        try:
            u_cfg = load_underlying_config()
        except UnderlyingConfigError as exc:
            logger.error("underlying config load failed: %s", exc)
            return

        tickers = self._underlying_tickers_for_market()
        if not tickers:
            logger.info("underlying poller: no inventory tickers")
            return
        poller = UnderlyingPoller(u_cfg, tickers=tickers)
        logger.info(
            "underlying poller started tickers=%s open_s=%s closed_s=%s",
            tickers,
            u_cfg.open_poll_interval_s,
            u_cfg.closed_poll_interval_s,
        )
        try:
            while not self._stop.is_set():
                try:
                    ticks = await asyncio.to_thread(poller.poll_once)
                    if ticks:
                        await asyncio.to_thread(
                            self.store.insert_underlying_prices, ticks
                        )
                        self.store.set_meta(
                            "underlying_last_poll_ms", str(now_ms())
                        )
                        self.store.set_meta(
                            "underlying_last_n", str(len(ticks))
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("underlying poll error: %s", exc)
                    await asyncio.to_thread(
                        self.store.insert_gap,
                        CollectorGap(
                            source="underlying",
                            gap_start_ms=now_ms(),
                            gap_end_ms=now_ms(),
                            detail=f"poll error: {exc}",
                        ),
                    )
                interval = poller.poll_interval_s()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                except TimeoutError:
                    pass
        finally:
            poller.close()

    async def _retention_loop(self) -> None:
        """Periodic prune under the store lock (WHI-751)."""
        cfg = self.cfg.retention
        if not cfg.enabled:
            logger.info("retention disabled in collector.yaml")
            return
        first = True
        while not self._stop.is_set():
            if not first:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=cfg.interval_s)
                    break
                except TimeoutError:
                    pass
            first = False
            try:
                was_paused = self._book_writes_paused
                report = await asyncio.to_thread(
                    self.store.run_retention, cfg, now_ms=now_ms()
                )
                self._book_writes_paused = report.book_writes_paused
                if report.book_writes_paused and not was_paused:
                    self._book_pause_logged = False
                    self._book_pause_started_ms = report.now_ms
                    logger.error(
                        "disk critical free=%s — book writes paused until "
                        "retention frees space",
                        report.free_bytes,
                    )
                elif not report.book_writes_paused and was_paused:
                    start = self._book_pause_started_ms or report.now_ms
                    self._book_pause_logged = False
                    self._book_pause_started_ms = None
                    self._book_gap_until_ms = report.now_ms + 5_000
                    await asyncio.to_thread(
                        self.store.insert_gap,
                        CollectorGap(
                            source="disk_critical",
                            gap_start_ms=start,
                            gap_end_ms=report.now_ms,
                            detail=(
                                f"book writes resumed free={report.free_bytes}"
                            ),
                        ),
                    )
                    logger.info("disk recovered — book writes resumed")
                elif report.disk_level != "ok":
                    logger.warning(
                        "disk %s free=%s deleted=%s",
                        report.disk_level,
                        report.free_bytes,
                        report.deleted,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.exception("retention error: %s", exc)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def run_forever(
    *,
    market_id: str | None = None,
    pairs_path: Path | None = None,
    collector_path: Path | None = None,
    sqlite_path: Path | None = None,
) -> None:
    load_dotenv()
    mid = market_id or DEFAULT_MARKET_ID
    pairs: PairsConfig | None = None
    bstocks: BStocksPairsConfig | None = None

    if pairs_path is not None:
        collector = load_collector_config(collector_path, market_id=mid)
        configured = sqlite_path or collector.resolved_sqlite_path()
        db_path = resolve_market_sqlite(market_id=mid, configured=configured)
        if collector.is_binance_pancake:
            bstocks = load_bstocks_pairs_config(pairs_path)
        else:
            pairs = load_pairs_config(pairs_path)
    else:
        ctx = load_market_context(
            mid,
            collector_path=collector_path,
            sqlite_path=sqlite_path,
        )
        if ctx.collector is None:
            raise SystemExit(
                f"market {mid!r} has no collector section in collector.yaml"
            )
        collector = ctx.collector
        db_path = ctx.sqlite_path
        if collector.is_binance_pancake:
            if ctx.bstocks is None:
                raise SystemExit(
                    f"market {mid!r} has no bStocks inventory for binance-pancake"
                )
            bstocks = ctx.bstocks
        else:
            if ctx.pairs is None:
                raise SystemExit(
                    f"market {mid!r} has no Bybit/Fluxion pairs inventory"
                )
            pairs = ctx.pairs

    _configure_logging(collector.logging.level)
    logger.info("collector market=%s sqlite=%s", mid, db_path)
    store = SqliteStore(db_path)
    daemon = CollectorDaemon(
        pairs, collector, store, bstocks=bstocks, market_id=mid
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _stop(*_: object) -> None:
        logger.info("signal received, shutting down")
        daemon.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_a: _stop())

    try:
        loop.run_until_complete(daemon.run())
    finally:
        store.close()
        loop.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Live collector: Bybit⇄Fluxion or Binance⇄Pancake → per-market SQLite"
        )
    )
    parser.add_argument(
        "--market",
        default=DEFAULT_MARKET_ID,
        help=f"Market id (default: {DEFAULT_MARKET_ID})",
    )
    parser.add_argument(
        "--pairs",
        type=Path,
        default=None,
        help="Path to market/pairs inventory (default: config/markets/{market}.yaml)",
    )
    parser.add_argument(
        "--collector-config",
        type=Path,
        default=None,
        help="Path to collector.yaml (default: config/collector.yaml)",
    )
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=None,
        help="Override SQLite path (default: markets.{id}.sqlite_path)",
    )
    args = parser.parse_args(argv)
    run_forever(
        market_id=args.market,
        pairs_path=args.pairs,
        collector_path=args.collector_config,
        sqlite_path=args.sqlite,
    )


if __name__ == "__main__":
    main()
