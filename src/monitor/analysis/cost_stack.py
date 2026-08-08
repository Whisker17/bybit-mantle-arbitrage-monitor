"""Corrected M0 cost-stack helpers for the WHI-909 edge-quant re-run.

Keeps offline study concerns (live USDCUSDT series, downtime inventory)
out of the pure window/threshold math in ``edge_quant`` and out of the
live capture path.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from monitor.analysis.edge_quant import usdc_premium_bps_from_mid
from monitor.metrics.config import MetricsConfig
from monitor.metrics.session import session_kind

# WHI-835 watchdog fix land time — downtime after this is the "is the
# collector healthy enough to re-run?" gate in WHI-909.
WHI835_LAND_MS = int(datetime(2026, 8, 4, 2, 35, tzinfo=UTC).timestamp() * 1000)

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
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

    # Walk minute by minute for RTH attribution (same resolution as edge quant).
    cur = datetime.fromtimestamp(since_ms / 1000, tz=UTC)
    end = datetime.fromtimestamp(until_ms / 1000, tz=UTC)
    step = timedelta(minutes=1)
    # day -> [total_gap_s, rth_s, rth_gap_s]
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


def kline_mid(open_: Decimal, high: Decimal, low: Decimal, close: Decimal) -> Decimal:
    """Minute mid proxy: average of high and low (Bybit spot kline)."""
    _ = open_, close
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
        o = Decimal(str(row[1]))
        h = Decimal(str(row[2]))
        lo = Decimal(str(row[3]))
        c = Decimal(str(row[4]))
        if h <= 0 or lo <= 0:
            continue
        out.append((start_ms, kline_mid(o, h, lo, c)))
    out.sort(key=lambda x: x[0])
    return out


def fetch_usdcusdt_klines(
    *,
    start_ms: int,
    end_ms: int,
    symbol: str = DEFAULT_USDCUSDT_SYMBOL,
    interval: str = "1",
    timeout_s: float = 30.0,
) -> list[tuple[int, Decimal]]:
    """Pull USDCUSDT 1m klines from Bybit public REST (paginated).

    Bybit returns at most 1000 candles per call, **newest-first** inside the
    requested window. We walk **backward** from ``end_ms`` so the full span is
    covered (a forward walk only ever sees the last 1000 bars).
    """
    if end_ms < start_ms:
        return []
    all_rows: list[tuple[int, Decimal]] = []
    cursor_end = end_ms
    # Safety cap: ~14 days of 1m bars at 1000/page.
    for _ in range(30):
        if cursor_end < start_ms:
            break
        params = urllib.parse.urlencode(
            {
                "category": "spot",
                "symbol": symbol,
                "interval": interval,
                "start": str(start_ms),
                "end": str(cursor_end),
                "limit": "1000",
            }
        )
        url = f"{BYBIT_KLINE_URL}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "whi-909-edge-quant/1"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Bybit kline fetch failed: {exc}") from exc
        if int(payload.get("retCode", -1)) != 0:
            raise RuntimeError(
                f"Bybit kline error: {payload.get('retCode')} {payload.get('retMsg')}"
            )
        batch = parse_bybit_klines(payload)
        if not batch:
            break
        all_rows.extend(batch)
        oldest = batch[0][0]
        # Move end cursor strictly before the oldest bar we already have.
        nxt_end = oldest - 1
        if nxt_end >= cursor_end:
            break
        cursor_end = nxt_end
        if len(batch) < 1000:
            break
    # Dedupe by ts (overlapping pages).
    by_ts: dict[int, Decimal] = {}
    for ts, mid in all_rows:
        if start_ms <= ts <= end_ms:
            by_ts[ts] = mid
    return sorted(by_ts.items(), key=lambda x: x[0])


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


def load_or_fetch_basis_series(
    *,
    start_ms: int,
    end_ms: int,
    cache_path: Path | None = None,
    force_fetch: bool = False,
    symbol: str = DEFAULT_USDCUSDT_SYMBOL,
) -> BasisSeries:
    """Load cached USDCUSDT basis series or fetch from Bybit and optionally cache."""
    if cache_path is not None and cache_path.is_file() and not force_fetch:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        mids = [
            (int(row["ts_ms"]), Decimal(str(row["mid"])))
            for row in raw.get("mids", [])
            if start_ms <= int(row["ts_ms"]) <= end_ms
        ]
        if mids:
            return basis_series_from_mids(
                mids, source=f"cache:{cache_path.name}", symbol=symbol
            )
    mids = fetch_usdcusdt_klines(start_ms=start_ms, end_ms=end_ms, symbol=symbol)
    series = basis_series_from_mids(
        mids, source="bybit_rest_kline_1m", symbol=symbol
    )
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "symbol": symbol,
            "interval": "1",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "mids": [
                {"ts_ms": ts, "mid": format(mid, "f")} for ts, mid in mids
            ],
        }
        cache_path.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
    return series


__all__ = [
    "WHI835_LAND_MS",
    "BasisSeries",
    "DayDowntime",
    "basis_series_from_mids",
    "downtime_by_day",
    "fetch_usdcusdt_klines",
    "kline_mid",
    "load_or_fetch_basis_series",
    "parse_bybit_klines",
]
