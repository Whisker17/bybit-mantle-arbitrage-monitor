"""Live Mantle chain-only latency probe (WHI-749).

Runs ``ChainPoller`` against the same pair inventory / RPC settings as the
collector (Bybit + RFQ loops skipped) and prints block_ts→recv latency
distributions plus gap counts.

Examples::

    # 10 min, current collector.yaml head_lag / poll interval
    uv run python -m monitor.collector.latency_probe --duration-s 600

    # Compare head_lag=1
    uv run python -m monitor.collector.latency_probe --duration-s 600 \\
        --head-lag-blocks 1 --out /tmp/probe-lag1.md

    # Offline analyze fluxion_pool_state from a monitor.db
    uv run python -m monitor.collector.latency_probe --analyze-db data/monitor.db
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from monitor.collector.config import (
    load_collector_config,
    load_dotenv,
    resolve_mantle_rpc_url,
    rpc_url_kind,
)
from monitor.collector.latency import (
    BlockIngestSample,
    GapSample,
    ProbeResult,
    format_probe_result,
    samples_from_pool_state_rows,
)
from monitor.fluxion.chain import ChainPoller
from monitor.fluxion.pools import PoolMeta
from monitor.fluxion.rpc import Rpc
from monitor.quotes import CollectorGap, now_ms
from monitor.symbols import load_pairs_config

logger = logging.getLogger(__name__)


def _rpc_host(url: str) -> str:
    try:
        return urlparse(url).hostname or url[:48]
    except Exception:  # noqa: BLE001
        return url[:48]


def run_probe(
    *,
    duration_s: float,
    head_lag_blocks: int,
    block_poll_interval_s: float,
    warmup_blocks: int,
    rpc_url: str,
    rpc_kind: str,
    pools: list[PoolMeta],
    lop_address: str,
    multicall3: str,
    rpc_min_interval_s: float,
    rpc_timeout_s: float,
    rpc_retries: int,
    max_block_gap: int,
    max_catchup_blocks: int,
    fetch_swap_receipts: bool,
) -> ProbeResult:
    samples: list[BlockIngestSample] = []
    gaps: list[GapSample] = []
    seq = 0

    def on_block_done(
        block_number: int, block_ts: int, discovered_ms: int, recv_ts_ms: int
    ) -> None:
        nonlocal seq
        sample = BlockIngestSample.from_timing(
            block_number=block_number,
            block_ts=block_ts,
            recv_ts_ms=recv_ts_ms,
            discovered_ms=discovered_ms,
            seq=seq,
        )
        seq += 1
        samples.append(sample)
        logger.info(
            "block=%s latency_ms=%s poll_wait_ms=%s rpc_work_ms=%s",
            block_number,
            sample.latency_ms,
            sample.poll_wait_ms,
            sample.rpc_work_ms,
        )

    def on_gap(gap: CollectorGap) -> None:
        g = GapSample(
            source=gap.source,
            gap_start_ms=gap.gap_start_ms,
            gap_end_ms=gap.gap_end_ms,
            detail=gap.detail,
        )
        gaps.append(g)
        logger.warning("gap source=%s detail=%s", g.source, g.detail)

    rpc = Rpc(
        rpc_url,
        multicall3=multicall3,
        min_interval=rpc_min_interval_s,
        timeout=rpc_timeout_s,
        retries=rpc_retries,
    )
    poller = ChainPoller(
        rpc,
        pools=pools,
        lop_address=lop_address,
        head_lag_blocks=head_lag_blocks,
        max_block_gap=max_block_gap,
        max_catchup_blocks=max_catchup_blocks,
        fetch_swap_receipts=fetch_swap_receipts,
        on_gap=on_gap,
        on_block_done=on_block_done,
    )

    t0 = time.monotonic()
    deadline = t0 + duration_s
    try:
        while time.monotonic() < deadline:
            try:
                poller.poll_once()
            except Exception as exc:  # noqa: BLE001
                logger.exception("poll error: %s", exc)
                now = now_ms()
                gaps.append(
                    GapSample(
                        source="mantle_blocks",
                        gap_start_ms=now,
                        gap_end_ms=now,
                        detail=f"poll error: {exc}",
                    )
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(block_poll_interval_s, remaining))
    finally:
        rpc.close()

    return ProbeResult(
        rpc_kind=rpc_kind,
        rpc_host=_rpc_host(rpc_url),
        head_lag_blocks=head_lag_blocks,
        block_poll_interval_s=block_poll_interval_s,
        duration_s=time.monotonic() - t0,
        warmup_blocks=warmup_blocks,
        samples=samples,
        gaps=gaps,
    )


def analyze_db(path: Path) -> tuple[list[BlockIngestSample], list[GapSample]]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            SELECT block_number, block_ts, MAX(recv_ts_ms) AS recv_ts_ms
            FROM fluxion_pool_state
            GROUP BY block_number, block_ts
            ORDER BY block_number
            """
        ).fetchall()
        samples = samples_from_pool_state_rows(
            (int(r[0]), int(r[1]), int(r[2])) for r in rows
        )
        gap_rows = conn.execute(
            """
            SELECT source, gap_start_ms, gap_end_ms, detail
            FROM collector_gaps
            WHERE source = 'mantle_blocks'
            ORDER BY gap_start_ms
            """
        ).fetchall()
        gaps = [
            GapSample(
                source=str(r[0]),
                gap_start_ms=int(r[1]),
                gap_end_ms=int(r[2]),
                detail=str(r[3] or ""),
            )
            for r in gap_rows
        ]
        return samples, gaps
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure Mantle block_ts→recv ingest latency (WHI-749)"
    )
    parser.add_argument(
        "--duration-s",
        type=float,
        default=600.0,
        help="Live probe wall seconds (default 600 = 10 min)",
    )
    parser.add_argument(
        "--head-lag-blocks",
        type=int,
        default=None,
        help="Override mantle.head_lag_blocks (default: collector.yaml)",
    )
    parser.add_argument(
        "--block-poll-interval-s",
        type=float,
        default=None,
        help="Override mantle.block_poll_interval_s",
    )
    parser.add_argument(
        "--warmup-blocks",
        type=int,
        default=30,
        help="First N blocks treated as startup window (default 30)",
    )
    parser.add_argument(
        "--public-rpc",
        action="store_true",
        help="Force public https://rpc.mantle.xyz instead of MANTLE_RPC_URL",
    )
    parser.add_argument(
        "--market",
        default=None,
        help="Market id (default: bybit-fluxion)",
    )
    parser.add_argument(
        "--collector-config",
        type=Path,
        default=None,
        help="Path to collector.yaml",
    )
    parser.add_argument(
        "--pairs-config",
        type=Path,
        default=None,
        help="Path to market inventory (default: config/markets/{market}.yaml)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write markdown report to this path",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write raw samples/gaps JSON to this path",
    )
    parser.add_argument(
        "--analyze-db",
        type=Path,
        default=None,
        help="Skip live probe; analyze fluxion_pool_state in this SQLite",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs — keyed Mantle paths embed the API key.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    from monitor.markets import DEFAULT_MARKET_ID

    load_dotenv()
    mid = args.market or DEFAULT_MARKET_ID
    cfg = load_collector_config(args.collector_config, market_id=mid)
    pairs = load_pairs_config(args.pairs_config, market_id=mid)

    if args.analyze_db is not None:
        samples, gaps = analyze_db(args.analyze_db)
        result = ProbeResult(
            rpc_kind="unknown",
            rpc_host=str(args.analyze_db),
            head_lag_blocks=-1,
            block_poll_interval_s=-1.0,
            duration_s=0.0,
            warmup_blocks=args.warmup_blocks,
            samples=samples,
            gaps=gaps,
        )
        text = format_probe_result(result)
        print(text)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        return 0

    if cfg.mantle is None:
        raise SystemExit("latency_probe requires bybit-fluxion (mantle) collector config")
    mantle = cfg.mantle
    head_lag = (
        args.head_lag_blocks
        if args.head_lag_blocks is not None
        else mantle.head_lag_blocks
    )
    poll_iv = (
        args.block_poll_interval_s
        if args.block_poll_interval_s is not None
        else mantle.block_poll_interval_s
    )
    if args.public_rpc:
        rpc_url = mantle.public_rpc_url
    else:
        rpc_url = resolve_mantle_rpc_url(mantle.public_rpc_url)
    kind = rpc_url_kind(rpc_url)

    pools = [
        PoolMeta(
            pair_id=p.id,
            pool=p.fluxion.amm.pool,
            wrapper_token=p.fluxion.wrapper_token,
            native_token=p.fluxion.native_token,
            quote_token=p.fluxion.quote_token_address,
            native_decimals=p.fluxion.native_decimals,
        )
        for p in pairs.pairs_with_amm()
        if p.fluxion.amm is not None
    ]
    logger.info(
        "probe start duration_s=%s head_lag=%s poll_s=%s pools=%s rpc=%s(%s)",
        args.duration_s,
        head_lag,
        poll_iv,
        len(pools),
        kind,
        _rpc_host(rpc_url),
    )

    result = run_probe(
        duration_s=args.duration_s,
        head_lag_blocks=head_lag,
        block_poll_interval_s=poll_iv,
        warmup_blocks=args.warmup_blocks,
        rpc_url=rpc_url,
        rpc_kind=kind,
        pools=pools,
        lop_address=pairs.contracts.limit_order_protocol,
        multicall3=mantle.multicall3,
        rpc_min_interval_s=mantle.rpc_min_interval_s,
        rpc_timeout_s=mantle.rpc_timeout_s,
        rpc_retries=mantle.rpc_retries,
        max_block_gap=mantle.max_block_gap,
        max_catchup_blocks=mantle.max_catchup_blocks,
        fetch_swap_receipts=mantle.fetch_swap_receipts,
    )
    text = format_probe_result(result)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        logger.info("wrote %s", args.out)
    if args.json_out:
        payload = {
            "rpc_kind": result.rpc_kind,
            "rpc_host": result.rpc_host,
            "head_lag_blocks": result.head_lag_blocks,
            "block_poll_interval_s": result.block_poll_interval_s,
            "duration_s": result.duration_s,
            "warmup_blocks": result.warmup_blocks,
            "samples": [
                {
                    "block_number": s.block_number,
                    "block_ts": s.block_ts,
                    "recv_ts_ms": s.recv_ts_ms,
                    "latency_ms": s.latency_ms,
                    "discovered_ms": s.discovered_ms,
                    "poll_wait_ms": s.poll_wait_ms,
                    "rpc_work_ms": s.rpc_work_ms,
                    "seq": s.seq,
                }
                for s in result.samples
            ],
            "gaps": [
                {
                    "source": g.source,
                    "gap_start_ms": g.gap_start_ms,
                    "gap_end_ms": g.gap_end_ms,
                    "detail": g.detail,
                    "is_block_not_found": g.is_block_not_found,
                }
                for g in result.gaps
            ],
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        logger.info("wrote %s", args.json_out)

    # Exit non-zero if zero samples (probe failed to process any block).
    if not result.samples:
        logger.error("no blocks processed")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
