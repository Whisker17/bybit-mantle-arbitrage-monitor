"""Block ingest latency samples and percentile reports (WHI-749).

Metric (matches daemon ``last_block_ingest_latency_ms``)::

    latency_ms = block_ingest_latency_ms(block_ts, recv_ts_ms)

where ``block_ts`` is the on-chain block timestamp (seconds) and ``recv_ts_ms``
is wall clock **after** all per-block RPC (getBlock + pool Multicall + logs +
optional receipts) completes — see ``ChainPoller._process_block``.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field


def block_ingest_latency_ms(block_ts: int, recv_ts_ms: int) -> int:
    """``recv_ts_ms - block_ts*1000``, floored at 0 (never negative from skew)."""
    return max(0, int(recv_ts_ms) - int(block_ts) * 1000)


@dataclass(frozen=True, slots=True)
class BlockIngestSample:
    """One successfully processed Mantle block."""

    block_number: int
    block_ts: int  # chain seconds
    recv_ts_ms: int  # wall ms after RPC
    latency_ms: int
    # Wall ms at the start of this ``_process_block`` attempt (pre-getBlock).
    # Re-stamped on every not-found retry — so poll_wait is last-attempt age,
    # not first-discovery age (docs/references/m2-block-ingest-latency.md).
    discovered_ms: int | None = None
    # 0-based index in the probe session (for startup vs steady split).
    seq: int = 0

    @classmethod
    def from_timing(
        cls,
        *,
        block_number: int,
        block_ts: int,
        recv_ts_ms: int,
        discovered_ms: int | None = None,
        seq: int = 0,
    ) -> BlockIngestSample:
        return cls(
            block_number=block_number,
            block_ts=block_ts,
            recv_ts_ms=recv_ts_ms,
            latency_ms=block_ingest_latency_ms(block_ts, recv_ts_ms),
            discovered_ms=discovered_ms,
            seq=seq,
        )

    @property
    def poll_wait_ms(self) -> int | None:
        """Age of the block when getBlock was attempted (before RPC work)."""
        if self.discovered_ms is None:
            return None
        return max(0, self.discovered_ms - self.block_ts * 1000)

    @property
    def rpc_work_ms(self) -> int | None:
        """recv_ts - discovered: RPC + decode path for this block."""
        if self.discovered_ms is None:
            return None
        return max(0, self.recv_ts_ms - self.discovered_ms)


@dataclass(frozen=True, slots=True)
class GapSample:
    source: str
    gap_start_ms: int
    gap_end_ms: int
    detail: str

    @property
    def is_block_not_found(self) -> bool:
        return "not found" in self.detail.lower()


@dataclass(frozen=True, slots=True)
class PercentileReport:
    """Equal-weight float percentiles for ingest latency samples.

    Deliberately separate from ``monitor.metrics.stats.Distribution`` (Decimal,
    time-weighted edge series) — collectors must not import metrics.
    """

    count: int
    p50: float | None
    p95: float | None
    p99: float | None
    max: float | None
    mean: float | None

    @classmethod
    def empty(cls) -> PercentileReport:
        return cls(count=0, p50=None, p95=None, p99=None, max=None, mean=None)

    @classmethod
    def from_values(cls, values: Sequence[float | int]) -> PercentileReport:
        if not values:
            return cls.empty()
        floats = [float(v) for v in values]
        return cls(
            count=len(floats),
            p50=_percentile(floats, 50),
            p95=_percentile(floats, 95),
            p99=_percentile(floats, 99),
            max=max(floats),
            mean=statistics.fmean(floats),
        )


# When attributing gaps to a sample window, include gaps that start slightly
# after the last sample recv (poll delay / log lag). Not a retention TTL.
_GAP_WINDOW_SLACK_MS = 60_000


@dataclass(frozen=True, slots=True)
class LatencyWindowReport:
    """Latency distribution over a sample window (startup or steady)."""

    label: str
    samples: int
    latency: PercentileReport
    poll_wait: PercentileReport
    rpc_work: PercentileReport
    block_not_found_gaps: int
    other_gaps: int
    # Max hole in block_number series within this window (0 = contiguous).
    max_block_number_gap: int = 0


@dataclass
class ProbeResult:
    """Full probe session outcome."""

    rpc_kind: str
    rpc_host: str
    head_lag_blocks: int
    block_poll_interval_s: float
    duration_s: float
    warmup_blocks: int
    samples: list[BlockIngestSample] = field(default_factory=list)
    gaps: list[GapSample] = field(default_factory=list)

    def window(
        self, *, label: str, samples: Sequence[BlockIngestSample]
    ) -> LatencyWindowReport:
        lat = [s.latency_ms for s in samples]
        waits = [s.poll_wait_ms for s in samples if s.poll_wait_ms is not None]
        works = [s.rpc_work_ms for s in samples if s.rpc_work_ms is not None]
        # Gaps are global to the session; attribute by time range if possible.
        if samples:
            t0 = min(s.recv_ts_ms for s in samples)
            t1 = max(s.recv_ts_ms for s in samples)
            window_gaps = [
                g
                for g in self.gaps
                if t0 <= g.gap_start_ms <= t1 + _GAP_WINDOW_SLACK_MS
            ]
        else:
            window_gaps = list(self.gaps)
        bnf = sum(1 for g in window_gaps if g.is_block_not_found)
        other = len(window_gaps) - bnf
        return LatencyWindowReport(
            label=label,
            samples=len(samples),
            latency=PercentileReport.from_values(lat),
            poll_wait=PercentileReport.from_values(waits),
            rpc_work=PercentileReport.from_values(works),
            block_not_found_gaps=bnf,
            other_gaps=other,
            max_block_number_gap=_max_block_number_gap(samples),
        )

    def split_reports(self) -> tuple[LatencyWindowReport, LatencyWindowReport]:
        """Startup = first ``warmup_blocks``; steady = the rest."""
        n = self.warmup_blocks
        startup = self.samples[:n]
        steady = self.samples[n:]
        return (
            self.window(label="startup", samples=startup),
            self.window(label="steady", samples=steady),
        )

    def all_report(self) -> LatencyWindowReport:
        return self.window(label="all", samples=self.samples)

    def block_not_found_count(self) -> int:
        return sum(1 for g in self.gaps if g.is_block_not_found)


def _percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile (inclusive). ``pct`` in [0, 100]."""
    if not values:
        raise ValueError("empty")
    xs = sorted(values)
    if pct <= 0:
        return xs[0]
    if pct >= 100:
        return xs[-1]
    # Nearest-rank: rank = ceil(p/100 * n), 1-indexed.
    rank = max(1, math.ceil(pct / 100.0 * len(xs)))
    return xs[rank - 1]


def samples_from_pool_state_rows(
    rows: Iterable[tuple[int, int, int]],
) -> list[BlockIngestSample]:
    """Build one sample per block from ``(block_number, block_ts, recv_ts_ms)``.

    When multiple pairs share a block, keep the first non-zero ``block_ts`` and
    ``max(recv_ts_ms)`` so multi-pair rows collapse to one latency sample.
    Callers that already ``GROUP BY block_number`` (e.g. analyze-db SQL) pass a
    single row per block; the collapse is then a no-op.
    """
    by_block: dict[int, tuple[int, int]] = {}
    for block_number, block_ts, recv_ts_ms in rows:
        prev = by_block.get(block_number)
        if prev is None:
            by_block[block_number] = (block_ts, recv_ts_ms)
        else:
            bt, rt = prev
            by_block[block_number] = (bt if bt else block_ts, max(rt, recv_ts_ms))
    return [
        BlockIngestSample.from_timing(
            block_number=block_number,
            block_ts=by_block[block_number][0],
            recv_ts_ms=by_block[block_number][1],
            seq=seq,
        )
        for seq, block_number in enumerate(sorted(by_block))
    ]


def _max_block_number_gap(samples: Sequence[BlockIngestSample]) -> int:
    """Largest hole between successive sorted block numbers (0 if contiguous)."""
    if len(samples) < 2:
        return 0
    nums = sorted(s.block_number for s in samples)
    return max((b - a - 1 for a, b in zip(nums[:-1], nums[1:], strict=True)), default=0)


def format_window(report: LatencyWindowReport) -> str:
    def _fmt(p: PercentileReport) -> str:
        if p.count == 0:
            return "n=0"
        return (
            f"n={p.count} p50={p.p50:.0f} p95={p.p95:.0f} p99={p.p99:.0f} "
            f"max={p.max:.0f} mean={p.mean:.0f}"
        )

    lines = [
        f"### {report.label} (samples={report.samples})",
        f"- latency_ms: {_fmt(report.latency)}",
        f"- poll_wait_ms: {_fmt(report.poll_wait)}",
        f"- rpc_work_ms: {_fmt(report.rpc_work)}",
        f"- block-not-found gaps: {report.block_not_found_gaps}",
        f"- other gaps: {report.other_gaps}",
        f"- max_block_number_gap: {report.max_block_number_gap}",
    ]
    return "\n".join(lines)


class LatencyTracker:
    """Rolling equal-weight latency window for collector meta (WHI-749).

    Daemon keeps only the last ``window`` samples so operators can read
    ``block_ingest_latency_p50_ms`` / ``_p95_ms`` / ``_p99_ms`` without a
    full probe. Not time-weighted — one sample per successfully ingested block.
    """

    def __init__(self, window: int = 256) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        self.window = window
        self._samples: list[int] = []

    def add(self, latency_ms: int) -> PercentileReport:
        self._samples.append(max(0, int(latency_ms)))
        if len(self._samples) > self.window:
            self._samples = self._samples[-self.window :]
        return self.report()

    def report(self) -> PercentileReport:
        return PercentileReport.from_values(self._samples)


def format_probe_result(result: ProbeResult) -> str:
    startup, steady = result.split_reports()
    all_r = result.all_report()
    lines = [
        "# Block ingest latency probe",
        "",
        f"- rpc: {result.rpc_kind} ({result.rpc_host})",
        f"- head_lag_blocks: {result.head_lag_blocks}",
        f"- block_poll_interval_s: {result.block_poll_interval_s}",
        f"- duration_s: {result.duration_s:.1f}",
        f"- warmup_blocks: {result.warmup_blocks}",
        f"- total samples: {len(result.samples)}",
        f"- total gaps: {len(result.gaps)} "
        f"(block-not-found={result.block_not_found_count()})",
        "",
        format_window(all_r),
        "",
        format_window(startup),
        "",
        format_window(steady),
    ]
    return "\n".join(lines)
