"""Corrected M0 cost-stack helpers for the WHI-909 edge-quant re-run.

Pure helpers only: downtime inventory, kline mid/parse, basis-series assembly.
HTTP fetch of USDCUSDT klines lives in the driver script
(``scripts/xstocks_edge_quant.py``) — this module never opens a network socket
or writes files (same contract as ``fill_validation`` / ``delay_decay``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from monitor.analysis.edge_quant import usdc_premium_bps_from_mid
from monitor.metrics.config import MetricsConfig
from monitor.metrics.session import session_kind

# WHI-835 watchdog fix land time — downtime after this is the "is the
# collector healthy enough to re-run?" gate in WHI-909.
WHI835_LAND_MS = int(datetime(2026, 8, 4, 2, 35, tzinfo=UTC).timestamp() * 1000)

DEFAULT_USDCUSDT_SYMBOL = "USDCUSDT"


@dataclass(frozen=True, slots=True)
class BasisSeries:
    """Sorted (ts_ms, premium_bps) series from USDCUSDT mids."""

    ts_ms: tuple[int, ...]
    bps: tuple[Decimal, ...]
    source: str
    symbol: str = DEFAULT_USDCUSDT_SYMBOL

    def __post_init__(self) -> None:
        if len(self.ts_ms) != len(self.bps):
            raise ValueError("basis series length mismatch")

    @property
    def n(self) -> int:
        return len(self.ts_ms)

    def summary(self) -> dict[str, Any]:
        if not self.bps:
            return {
                "n": 0,
                "source": self.source,
                "symbol": self.symbol,
                "min_bps": None,
                "max_bps": None,
                "median_bps": None,
                "mean_bps": None,
            }
        ordered = sorted(self.bps)
        n = len(ordered)
        mid = n // 2
        if n % 2:
            median = ordered[mid]
        else:
            median = (ordered[mid - 1] + ordered[mid]) / 2
        mean = sum(ordered, Decimal(0)) / Decimal(n)
        return {
            "n": n,
            "source": self.source,
            "symbol": self.symbol,
            "min_bps": str(ordered[0]),
            "max_bps": str(ordered[-1]),
            "median_bps": str(median),
            "mean_bps": format(mean, "f"),
            "first_ts_ms": self.ts_ms[0],
            "last_ts_ms": self.ts_ms[-1],
        }


@dataclass(frozen=True, slots=True)
class DayDowntime:
    day: str  # YYYY-MM-DD UTC
    total_gap_hours: float
    rth_hours: float
    rth_gap_hours: float

    @property
    def rth_clean_hours(self) -> float:
        return max(0.0, self.rth_hours - self.rth_gap_hours)


def _in_gap(ts_ms: int, gaps: Sequence[tuple[int, int]]) -> bool:
    for start, end in gaps:
        if start <= ts_ms <= end:
            return True
        if start > ts_ms:
            break
    return False


def downtime_by_day(
    gaps: Sequence[tuple[int, int]],
    *,
    since_ms: int,
    until_ms: int,
    metrics_cfg: MetricsConfig,
) -> list[DayDowntime]:
    """Per-UTC-day gap hours and RTH loss between ``since_ms`` and ``until_ms``.

    ``gaps`` are ``(start_ms, end_ms)`` sorted by start. Only the intersection
    with ``[since_ms, until_ms]`` is counted.
    """
    if until_ms <= since_ms:
        return []
    clipped: list[tuple[int, int]] = []
    for s, e in gaps:
        cs = max(s, since_ms)
        ce = min(e, until_ms)
        if ce > cs:
            clipped.append((cs, ce))
    clipped.sort()

    cur = datetime.fromtimestamp(since_ms / 1000, tz=UTC)
    end = datetime.fromtimestamp(until_ms / 1000, tz=UTC)
    step = timedelta(minutes=1)
    acc: dict[str, list[float]] = {}
    while cur < end:
        nxt = min(end, cur + step)
        dt = (nxt - cur).total_seconds()
        ts_ms = int(cur.timestamp() * 1000)
        day = cur.strftime("%Y-%m-%d")
        bucket = acc.setdefault(day, [0.0, 0.0, 0.0])
        sk = session_kind(cur, config=metrics_cfg).value
        g = _in_gap(ts_ms, clipped)
        if g:
            bucket[0] += dt
        if sk == "open":
            bucket[1] += dt
            if g:
                bucket[2] += dt
        cur = nxt

    out: list[DayDowntime] = []
    for day in sorted(acc):
        total_s, rth_s, rth_gap_s = acc[day]
        out.append(
            DayDowntime(
                day=day,
                total_gap_hours=total_s / 3600.0,
                rth_hours=rth_s / 3600.0,
                rth_gap_hours=rth_gap_s / 3600.0,
            )
        )
    return out


def kline_mid(high: Decimal, low: Decimal) -> Decimal:
    """Minute mid proxy: average of high and low (Bybit spot kline)."""
    return (high + low) / Decimal(2)


def parse_bybit_klines(payload: dict[str, Any]) -> list[tuple[int, Decimal]]:
    """Parse Bybit v5 kline ``result.list`` → sorted (start_ms, mid)."""
    result = payload.get("result") or {}
    rows = result.get("list") or []
    out: list[tuple[int, Decimal]] = []
    for row in rows:
        # [start, open, high, low, close, volume, turnover]
        if len(row) < 5:
            continue
        start_ms = int(row[0])
        h = Decimal(str(row[2]))
        lo = Decimal(str(row[3]))
        if h <= 0 or lo <= 0:
            continue
        out.append((start_ms, kline_mid(h, lo)))
    out.sort(key=lambda x: x[0])
    return out


def basis_series_from_mids(
    mids: Sequence[tuple[int, Decimal]],
    *,
    source: str,
    symbol: str = DEFAULT_USDCUSDT_SYMBOL,
) -> BasisSeries:
    ts_list: list[int] = []
    bps_list: list[Decimal] = []
    for ts, mid in mids:
        ts_list.append(ts)
        bps_list.append(usdc_premium_bps_from_mid(mid))
    return BasisSeries(
        ts_ms=tuple(ts_list),
        bps=tuple(bps_list),
        source=source,
        symbol=symbol,
    )


__all__ = [
    "WHI835_LAND_MS",
    "DEFAULT_USDCUSDT_SYMBOL",
    "BasisSeries",
    "DayDowntime",
    "basis_series_from_mids",
    "downtime_by_day",
    "kline_mid",
    "parse_bybit_klines",
]
