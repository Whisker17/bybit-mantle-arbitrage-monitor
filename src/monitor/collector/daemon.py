"""Orchestrate Bybit WS + Mantle block poll + RFQ poll → SQLite."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from monitor.bybit.ws import BybitWsCollector
from monitor.collector.config import (
    CollectorConfig,
    load_collector_config,
    load_dotenv,
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
    CollectorGap,
    FluxionPoolStateTick,
    FluxionRfqFillTick,
    FluxionSwapTick,
    now_ms,
)
from monitor.storage import SqliteStore
from monitor.symbols import load_pairs_config
from monitor.symbols.models import PairsConfig

logger = logging.getLogger(__name__)


class CollectorDaemon:
    """Long-running realtime collector (no historical backfill)."""

    def __init__(
        self,
        pairs: PairsConfig,
        collector: CollectorConfig,
        store: SqliteStore,
        *,
        rpc_url: str | None = None,
    ) -> None:
        self.pairs = pairs
        self.cfg = collector
        self.store = store
        self.rpc_url = rpc_url or resolve_mantle_rpc_url(
            collector.mantle.public_rpc_url
        )
        self._stop = asyncio.Event()
        self._rfq_gap = False
        # Set by retention loop under disk-critical waterline (WHI-751).
        self._book_writes_paused = False
        self._book_pause_logged = False
        self._book_pause_started_ms: int | None = None
        # After resume, mark all pairs' books gap=1 until this wall-clock ms
        # (mirrors BybitWsCollector post-reconnect gap window).
        self._book_gap_until_ms: int = 0
        # Last written L1 row fingerprint — skip duplicate rows under orderbook.50
        # even while sticky gap=True (include gap so first gapped tick still lands).
        self._last_book_l1: dict[str, tuple[Decimal, Decimal, bool]] = {}

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        pair_id_by_symbol = {p.bybit.symbol.upper(): p.id for p in self.pairs.pairs}
        mult_by_symbol = {
            p.bybit.symbol.upper(): p.bybit.multiplier for p in self.pairs.pairs
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
            # on_depth None disables VWAP path in the WS collector.
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
            asyncio.create_task(self._chain_loop(), name="mantle_chain"),
            asyncio.create_task(self._rfq_loop(), name="rfq_poll"),
            asyncio.create_task(self._retention_loop(), name="retention"),
        ]
        self.store.set_meta("collector_started_ms", str(now_ms()))
        self.store.set_meta("rpc_url_kind", rpc_url_kind(self.rpc_url))
        logger.info(
            "collector started pairs=%d amm_pools=%d sqlite=%s rpc_kind=%s "
            "retention=%s bybit_book=%s depth=%s",
            len(self.pairs.pairs),
            len(self.pairs.pairs_with_amm()),
            self.store.path,
            rpc_url_kind(self.rpc_url),
            self.cfg.retention.enabled,
            self.cfg.bybit.book_topic_prefix,
            depth_cfg.enabled,
        )
        try:
            await self._stop.wait()
        finally:
            bybit.request_stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # If we shut down while disk-critical, still record the pause window.
            if self._book_writes_paused and self._book_pause_started_ms is not None:
                end = now_ms()
                self.store.insert_gap(
                    CollectorGap(
                        source="disk_critical",
                        gap_start_ms=self._book_pause_started_ms,
                        gap_end_ms=end,
                        detail="collector stopped while bybit_book writes paused",
                    )
                )
            self.store.set_meta("collector_stopped_ms", str(now_ms()))
            logger.info("collector stopped")

    async def _on_book(self, tick: BybitBookTick) -> None:
        if self._book_writes_paused:
            # One log line per pause episode — not per tick (~7+/s).
            if not self._book_pause_logged:
                logger.error(
                    "skip bybit_book writes: disk critical (retention pause active)"
                )
                self._book_pause_logged = True
            return
        if tick.recv_ts_ms < self._book_gap_until_ms or (
            not tick.gap and now_ms() < self._book_gap_until_ms
        ):
            tick = replace(tick, gap=True)
        # orderbook.50 fires often for non-L1 levels; only journal when L1 or
        # gap flag changes (sticky gap must not re-amplify full delta rate).
        key = (tick.bid, tick.ask, tick.gap)
        if self._last_book_l1.get(tick.pair_id) == key:
            return
        self._last_book_l1[tick.pair_id] = key
        await asyncio.to_thread(self.store.insert_bybit_book, [tick])

    async def _on_depth(self, tick: BybitDepthTick) -> None:
        """Persist a precomputed VWAP curve (already throttled in BybitWsCollector)."""
        if self._book_writes_paused:
            return
        if tick.recv_ts_ms < self._book_gap_until_ms or (
            not tick.gap and now_ms() < self._book_gap_until_ms
        ):
            tick = replace(tick, gap=True)
        await asyncio.to_thread(self.store.insert_bybit_depth, [tick])

    async def _on_trade(self, tick: BybitTradeTick) -> None:
        await asyncio.to_thread(self.store.insert_bybit_trades, [tick])

    async def _on_gap(self, gap: CollectorGap) -> None:
        await asyncio.to_thread(self.store.insert_gap, gap)
        logger.warning(
            "gap source=%s detail=%s duration_ms=%s",
            gap.source,
            gap.detail,
            gap.gap_end_ms - gap.gap_start_ms,
        )

    async def _chain_loop(self) -> None:
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

        # Rolling block_ts→recv latency (one sample per block via on_block_done).
        latency_tracker = LatencyTracker(
            window=self.cfg.mantle.latency_window_blocks
        )

        def on_state(ticks: list[FluxionPoolStateTick]) -> None:
            self.store.insert_pool_state(ticks)

        def on_swaps(ticks: list[FluxionSwapTick]) -> None:
            self.store.insert_swaps(ticks)

        def on_fills(ticks: list[FluxionRfqFillTick]) -> None:
            self.store.insert_rfq_fills(ticks)

        def on_gap(gap: CollectorGap) -> None:
            self.store.insert_gap(gap)
            logger.warning("chain gap: %s", gap.detail)

        def on_block_done(
            block_number: int, block_ts: int, _discovered_ms: int, recv_ts_ms: int
        ) -> None:
            latency_ms = block_ingest_latency_ms(block_ts, recv_ts_ms)
            report = latency_tracker.add(latency_ms)
            self.store.set_meta("last_block_ingest_latency_ms", str(latency_ms))
            self.store.set_meta("last_block", str(block_number))
            # Distribution meta — WHI-749; do not treat last_* as P95.
            # LatencyTracker.add always leaves count >= 1, so p50/p95/p99 are set.
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

        poller = ChainPoller(
            rpc,
            pools=pools,
            lop_address=self.pairs.contracts.limit_order_protocol,
            head_lag_blocks=self.cfg.mantle.head_lag_blocks,
            max_block_gap=self.cfg.mantle.max_block_gap,
            max_catchup_blocks=self.cfg.mantle.max_catchup_blocks,
            fetch_swap_receipts=self.cfg.mantle.fetch_swap_receipts,
            on_pool_state=on_state,
            on_swaps=on_swaps,
            on_rfq_fills=on_fills,
            on_gap=on_gap,
            on_block_done=on_block_done,
        )
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.to_thread(poller.poll_once)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("chain poll error: %s", exc)
                    await asyncio.to_thread(
                        self.store.insert_gap,
                        CollectorGap(
                            source="mantle_blocks",
                            gap_start_ms=now_ms(),
                            gap_end_ms=now_ms(),
                            detail=f"poll error: {exc}",
                        ),
                    )
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self.cfg.mantle.block_poll_interval_s,
                    )
                except TimeoutError:
                    pass
        finally:
            rpc.close()

    async def _rfq_loop(self) -> None:
        poller = RfqPoller(
            pairs=list(self.pairs.pairs),
            rfq=self.pairs.rfq,
            amount_usdc_raw=self.cfg.rfq.amount_usdc_raw,
            amount_native_raw=self.cfg.rfq.amount_native_raw,
            prefer_primary_url=self.cfg.rfq.prefer_primary_url,
            poll_both_sides=self.cfg.rfq.poll_both_sides,
            http_timeout_s=self.cfg.rfq.http_timeout_s,
        )
        # Fill the global budget: one HTTP call every 60/rate_limit seconds.
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

    async def _retention_loop(self) -> None:
        """Periodic prune under the store lock (WHI-751)."""
        cfg = self.cfg.retention
        if not cfg.enabled:
            logger.info("retention disabled in collector.yaml")
            return
        # First pass shortly after boot so a full disk is addressed without
        # waiting a full interval; subsequent passes honor interval_s.
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
                        "disk critical free=%s — bybit_book writes paused until "
                        "retention frees space",
                        report.free_bytes,
                    )
                elif not report.book_writes_paused and was_paused:
                    start = self._book_pause_started_ms or report.now_ms
                    self._book_pause_logged = False
                    self._book_pause_started_ms = None
                    # Cover all pairs for a few seconds (same idea as WS reconnect).
                    self._book_gap_until_ms = report.now_ms + 5_000
                    await asyncio.to_thread(
                        self.store.insert_gap,
                        CollectorGap(
                            source="disk_critical",
                            gap_start_ms=start,
                            gap_end_ms=report.now_ms,
                            detail=(
                                f"bybit_book writes resumed free={report.free_bytes}"
                            ),
                        ),
                    )
                    logger.info("disk recovered — bybit_book writes resumed")
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
    if pairs_path is not None:
        # Explicit inventory path (tests / overrides): still market-scope collector.
        pairs = load_pairs_config(pairs_path)
        collector = load_collector_config(collector_path, market_id=mid)
        configured = sqlite_path or collector.resolved_sqlite_path()
        # Convention-path-only legacy fallback (custom --sqlite never redirects).
        db_path = resolve_market_sqlite(market_id=mid, configured=configured)
    else:
        ctx = load_market_context(
            mid,
            collector_path=collector_path,
            sqlite_path=sqlite_path,
        )
        if ctx.pairs is None:
            raise SystemExit(
                f"market {mid!r} has no Bybit/Fluxion pairs inventory; "
                "collector runtime for this market is not wired yet (see M7-3)"
            )
        if ctx.collector is None:
            raise SystemExit(
                f"market {mid!r} has no collector section in collector.yaml"
            )
        pairs = ctx.pairs
        collector = ctx.collector
        db_path = ctx.sqlite_path

    _configure_logging(collector.logging.level)
    logger.info("collector market=%s sqlite=%s", mid, db_path)
    store = SqliteStore(db_path)
    daemon = CollectorDaemon(pairs, collector, store)

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
        description="M2 live collector: Bybit WS + Fluxion chain + RFQ → SQLite"
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
