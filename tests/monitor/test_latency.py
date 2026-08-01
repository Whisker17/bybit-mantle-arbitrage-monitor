"""Unit tests for block ingest latency stats (WHI-749)."""

from __future__ import annotations

from typing import Any

from monitor.collector.latency import (
    BlockIngestSample,
    GapSample,
    PercentileReport,
    ProbeResult,
    samples_from_pool_state_rows,
)
from monitor.fluxion.chain import ChainPoller


class _FakeRpc:
    def __init__(self, head: int = 100) -> None:
        self.head = head

    def block_number(self) -> int:
        return self.head

    def get_block(self, number: int, full_txs: bool = False) -> dict[str, Any] | None:
        return {"timestamp": hex(1_700_000_000 + number * 2)}

    def multicall(
        self, calls: list[Any], block: int | str = "latest", **_: Any
    ) -> list[Any]:
        return [(False, b"")] * len(calls)

    def get_logs(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def get_transaction_receipt(self, tx_hash: str) -> None:
        return None

    def eth_call(self, *args: Any, **kwargs: Any) -> str:
        return "0x" + "0" * 64


def test_latency_tracker_window() -> None:
    from monitor.collector.latency import LatencyTracker

    t = LatencyTracker(window=3)
    t.add(100)
    t.add(200)
    r = t.add(300)
    assert r.count == 3
    assert r.p50 == 200
    r = t.add(400)  # drops 100
    assert r.count == 3
    assert r.max == 400
    assert r.p50 == 300


def test_percentile_nearest_rank() -> None:
    # 1..100 → p50=50, p95=95, p99=99
    values = list(range(1, 101))
    r = PercentileReport.from_values(values)
    assert r.count == 100
    assert r.p50 == 50
    assert r.p95 == 95
    assert r.p99 == 99
    assert r.max == 100
    assert r.mean == 50.5


def test_percentile_empty() -> None:
    r = PercentileReport.from_values([])
    assert r.count == 0
    assert r.p50 is None


def test_samples_from_pool_state_rows_max_recv() -> None:
    rows = [
        (10, 1_700_000_000, 1_700_000_000_100),
        (10, 1_700_000_000, 1_700_000_000_500),  # slower pair
        (11, 1_700_000_002, 1_700_000_002_200),
    ]
    samples = samples_from_pool_state_rows(rows)
    assert len(samples) == 2
    assert samples[0].block_number == 10
    assert samples[0].latency_ms == 500
    assert samples[1].latency_ms == 200


def test_gap_sample_block_not_found() -> None:
    g = GapSample(
        source="mantle_blocks",
        gap_start_ms=1,
        gap_end_ms=2,
        detail="block 99 not found",
    )
    assert g.is_block_not_found
    g2 = GapSample("mantle_blocks", 1, 2, "lag=20 skipping to tip")
    assert not g2.is_block_not_found


def test_probe_result_startup_steady_split() -> None:
    samples = [
        BlockIngestSample(
            block_number=i,
            block_ts=1_700_000_000 + i * 2,
            recv_ts_ms=(1_700_000_000 + i * 2) * 1000 + (100 if i < 3 else 3000),
            latency_ms=100 if i < 3 else 3000,
            seq=i,
        )
        for i in range(10)
    ]
    result = ProbeResult(
        rpc_kind="keyed",
        rpc_host="rpc.example",
        head_lag_blocks=0,
        block_poll_interval_s=0.25,
        duration_s=60.0,
        warmup_blocks=3,
        samples=samples,
        gaps=[],
    )
    startup, steady = result.split_reports()
    assert startup.samples == 3
    assert steady.samples == 7
    assert startup.latency.p95 == 100
    assert steady.latency.p50 == 3000


def test_chain_poller_on_block_done() -> None:
    rpc = _FakeRpc(head=50)
    done: list[tuple[int, int, int, int]] = []
    poller = ChainPoller(
        rpc,  # type: ignore[arg-type]
        pools=[],
        lop_address="0x" + "11" * 20,
        head_lag_blocks=0,
        on_block_done=lambda b, ts, d, r: done.append((b, ts, d, r)),
    )
    assert poller.poll_once() == 1
    assert len(done) == 1
    block, block_ts, discovered, recv = done[0]
    assert block == 50
    assert block_ts == 1_700_000_000 + 50 * 2
    assert recv >= discovered


def test_chain_poller_head_lag_skips_tip() -> None:
    rpc = _FakeRpc(head=100)
    done: list[int] = []
    poller = ChainPoller(
        rpc,  # type: ignore[arg-type]
        pools=[],
        lop_address="0x" + "11" * 20,
        head_lag_blocks=1,
        on_block_done=lambda b, *_: done.append(b),
    )
    n = poller.poll_once()
    assert n == 1
    # Settled head = 99; processes block 99, not tip 100.
    assert done == [99]
    assert poller.last_block == 99
