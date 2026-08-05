#!/usr/bin/env python3
"""WHI-866 / M8: xStocks arb edge quantification & threshold fit (go/no-go).

Reads the bybit-fluxion collector journal, drives the PnL v2 cash-flow engine
at $500 / $1,000 notional for both directions, builds opportunity windows
under single-flight + re-entry cooldown, fits min_edge_bps (knee: highest
threshold retaining ≥70% of zero-threshold capturable profit), and writes:

  docs/references/m8-xstocks-edge-quant.md

Usage (repo root; journal must exist):

  uv run python scripts/xstocks_edge_quant.py
  uv run python scripts/xstocks_edge_quant.py --db data/monitor-bybit-fluxion.db
  uv run python scripts/xstocks_edge_quant.py --sample-ms 15000
"""

from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from monitor.analysis.edge_quant import (  # noqa: E402
    EdgeSample,
    OpportunityWindow,
    SweepRow,
    ThresholdFit,
    detect_windows,
    fit_min_edge_bps,
    portfolio_capturable_profit,
    threshold_sweep,
)
from monitor.markets import load_market_context  # noqa: E402
from monitor.metrics.amm_pool import amm_pool_from_pair_tick  # noqa: E402
from monitor.metrics.amm_quote import amm_quote_for_cex  # noqa: E402
from monitor.metrics.edge import mid_from_bid_ask  # noqa: E402
from monitor.metrics.pnl_snapshot import (  # noqa: E402
    levels_from_depth_curve,
    rfq_tick_to_poll_quote,
)
from monitor.metrics.pnl_v2 import compute_pnl_usd  # noqa: E402
from monitor.metrics.session import session_kind  # noqa: E402
from monitor.quotes import (  # noqa: E402
    BybitBookTick,
    BybitDepthTick,
    FluxionPoolStateTick,
    FluxionRfqQuoteTick,
)

# Row mappers are module-private on JournalReader but are the only typed
# SQLite→tick path; research scripts reuse them rather than re-parsing
# columns (same pattern as offline analysis elsewhere).
from monitor.storage.reader import (  # noqa: E402
    _row_to_bybit_book,
    _row_to_bybit_depth,
    _row_to_pool_state,
    _row_to_rfq_quote,
)
from monitor.symbols.models import Pair  # noqa: E402

# Bot DESIGN §1.4 gates.
GO_USDT_PER_DAY = Decimal("15")
NOGO_USDT_PER_DAY = Decimal("5")
GO_MIN_SYMBOLS = 3
INVENTORY_USD = Decimal("5000")
MAX_TRADE_USD = Decimal("1000")
NOTIONALS = (Decimal("500"), Decimal("1000"))
DIRECTIONS = ("buy_fluxion_sell_bybit", "buy_bybit_sell_fluxion")
THRESHOLD_BPS = [Decimal(i) for i in range(0, 62, 2)]
# Primary go/no-go uses one trade per window: cooldown larger than any
# realistic window so re-entry does not inflate capturable profit. Sensitivity
# with a live bot cooldown (30s) is still computed in the JSON series via
# --reentry-cooldown-ms.
DEFAULT_REENTRY_COOLDOWN_MS = 86_400_000  # 1 day → effectively one entry/window
DEFAULT_TRADE_DURATION_MS = 5_000
DEFAULT_MAX_GAP_MS = 120_000  # glue-break for window detection
DEFAULT_SAMPLE_MS = 10_000
DEFAULT_ALIGN_MS = 15_000  # max as-of age for pool / depth
DEFAULT_REPORT = _REPO / "docs" / "references" / "m8-xstocks-edge-quant.md"
DEFAULT_JSON = _REPO / "docs" / "references" / "m8-xstocks-edge-quant.json"


@dataclass(frozen=True, slots=True)
class GapInterval:
    start_ms: int
    end_ms: int
    source: str


@dataclass
class DataSpan:
    table: str
    n_rows: int
    min_ms: int | None
    max_ms: int | None


def _ms_iso(ms: int | None) -> str:
    if ms is None:
        return "n/a"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _in_gap(ts_ms: int, gaps: Sequence[GapInterval]) -> bool:
    # gaps sorted by start; linear scan is fine for ~20 collector_down rows.
    for g in gaps:
        if g.start_ms <= ts_ms <= g.end_ms:
            return True
        if g.start_ms > ts_ms:
            break
    return False


def _as_of_idx(ts_list: Sequence[int], ts_ms: int) -> int | None:
    """Rightmost index with ts_list[i] <= ts_ms, or None."""
    i = bisect.bisect_right(ts_list, ts_ms) - 1
    return i if i >= 0 else None


def load_gaps(conn: sqlite3.Connection) -> list[GapInterval]:
    rows = conn.execute(
        """
        SELECT source, gap_start_ms, gap_end_ms
        FROM collector_gaps
        WHERE source = 'collector_down'
        ORDER BY gap_start_ms
        """
    ).fetchall()
    return [
        GapInterval(start_ms=int(r[1]), end_ms=int(r[2]), source=str(r[0])) for r in rows
    ]


def table_span(conn: sqlite3.Connection, table: str, ts_col: str) -> DataSpan:
    row = conn.execute(
        f"SELECT COUNT(*), MIN({ts_col}), MAX({ts_col}) FROM {table}"
    ).fetchone()
    assert row is not None
    return DataSpan(
        table=table,
        n_rows=int(row[0] or 0),
        min_ms=None if row[1] is None else int(row[1]),
        max_ms=None if row[2] is None else int(row[2]),
    )


def per_pair_book_span(
    conn: sqlite3.Connection,
) -> list[tuple[str, int, int | None, int | None]]:
    rows = conn.execute(
        """
        SELECT pair_id, COUNT(*), MIN(recv_ts_ms), MAX(recv_ts_ms)
        FROM bybit_book
        GROUP BY pair_id
        ORDER BY pair_id
        """
    ).fetchall()
    out: list[tuple[str, int, int | None, int | None]] = []
    for r in rows:
        mn = None if r[2] is None else int(r[2])
        mx = None if r[3] is None else int(r[3])
        out.append((str(r[0]), int(r[1]), mn, mx))
    return out


def load_bucketed_books(
    conn: sqlite3.Connection,
    pair_id: str,
    *,
    sample_ms: int,
    since_ms: int,
    until_ms: int,
) -> list[BybitBookTick]:
    """One book tick per sample_ms bucket (latest recv in bucket)."""
    rows = conn.execute(
        """
        SELECT b.*
        FROM bybit_book b
        INNER JOIN (
            SELECT (recv_ts_ms / ?) * ? AS bucket, MAX(id) AS mid
            FROM bybit_book
            WHERE pair_id = ?
              AND recv_ts_ms >= ?
              AND recv_ts_ms <= ?
              AND gap = 0
            GROUP BY bucket
        ) t ON b.id = t.mid
        ORDER BY b.recv_ts_ms ASC
        """,
        (sample_ms, sample_ms, pair_id, since_ms, until_ms),
    ).fetchall()
    return [_row_to_bybit_book(r) for r in rows]


def load_pools(
    conn: sqlite3.Connection,
    pair_id: str,
    *,
    since_ms: int,
    until_ms: int,
) -> list[FluxionPoolStateTick]:
    rows = conn.execute(
        """
        SELECT * FROM fluxion_pool_state
        WHERE pair_id = ?
          AND recv_ts_ms >= ?
          AND recv_ts_ms <= ?
          AND gap = 0
        ORDER BY recv_ts_ms ASC
        """,
        (pair_id, since_ms, until_ms),
    ).fetchall()
    return [_row_to_pool_state(r) for r in rows]


def load_depths(
    conn: sqlite3.Connection,
    pair_id: str,
    *,
    since_ms: int,
    until_ms: int,
) -> list[BybitDepthTick]:
    # Depth is throttled (~1s); still large — bucket to sample_ms via join later
    # by loading full range for as-of (2d × 1Hz ≈ 170k/pair worst case).
    rows = conn.execute(
        """
        SELECT d.*
        FROM bybit_depth d
        INNER JOIN (
            SELECT (recv_ts_ms / 1000) * 1000 AS bucket, MAX(id) AS mid
            FROM bybit_depth
            WHERE pair_id = ?
              AND recv_ts_ms >= ?
              AND recv_ts_ms <= ?
              AND gap = 0
            GROUP BY bucket
        ) t ON d.id = t.mid
        ORDER BY d.recv_ts_ms ASC
        """,
        (pair_id, since_ms, until_ms),
    ).fetchall()
    return [_row_to_bybit_depth(r) for r in rows]


def load_rfq(
    conn: sqlite3.Connection,
    pair_id: str,
    *,
    since_ms: int,
    until_ms: int,
) -> list[FluxionRfqQuoteTick]:
    rows = conn.execute(
        """
        SELECT * FROM fluxion_rfq_quotes
        WHERE pair_id = ?
          AND poll_ts_ms >= ?
          AND poll_ts_ms <= ?
        ORDER BY poll_ts_ms ASC
        """,
        (pair_id, since_ms, until_ms),
    ).fetchall()
    return [_row_to_rfq_quote(r) for r in rows]


def rth_closed_hours(
    start_ms: int,
    end_ms: int,
    *,
    metrics_cfg: Any,
    gaps: Sequence[GapInterval],
) -> tuple[float, float, float, float]:
    """Return (rth_h, closed_h, rth_excl_gap_h, closed_excl_gap_h)."""
    from datetime import timedelta

    cur = datetime.fromtimestamp(start_ms / 1000, tz=UTC)
    end = datetime.fromtimestamp(end_ms / 1000, tz=UTC)
    rth = closed = rth_ok = closed_ok = 0.0
    step = timedelta(minutes=1)
    while cur < end:
        nxt = min(end, cur + step)
        dt = (nxt - cur).total_seconds()
        ts_ms = int(cur.timestamp() * 1000)
        sk = session_kind(cur, config=metrics_cfg)
        in_g = _in_gap(ts_ms, gaps)
        if sk.value == "open":
            rth += dt
            if not in_g:
                rth_ok += dt
        else:
            closed += dt
            if not in_g:
                closed_ok += dt
        cur = nxt
    return rth / 3600, closed / 3600, rth_ok / 3600, closed_ok / 3600


def build_amm_samples(
    *,
    pair: Pair,
    books: Sequence[BybitBookTick],
    pools: Sequence[FluxionPoolStateTick],
    depths: Sequence[BybitDepthTick],
    metrics_cfg: Any,
    quote_decimals: int,
    size_usd: Decimal,
    gaps: Sequence[GapInterval],
    align_ms: int,
    max_abs_spread_bps: Decimal | None,
) -> list[EdgeSample]:
    if not books or not pools:
        return []
    pool_ts = [p.recv_ts_ms for p in pools]
    depth_ts = [d.recv_ts_ms for d in depths]
    out: list[EdgeSample] = []
    for book in books:
        ts = book.recv_ts_ms
        if _in_gap(ts, gaps):
            continue
        pi = _as_of_idx(pool_ts, ts)
        if pi is None:
            continue
        pool = pools[pi]
        if ts - pool.recv_ts_ms > align_ms:
            continue
        amm = amm_pool_from_pair_tick(pair, pool, quote_decimals=quote_decimals)
        if amm is None:
            continue
        cex_mid = mid_from_bid_ask(book.bid_de_multiplied, book.ask_de_multiplied)
        if cex_mid is None or cex_mid <= 0:
            continue
        _, reason = amm_quote_for_cex(
            pool,
            cex_mid=cex_mid,
            max_abs_spread_bps=max_abs_spread_bps,
        )
        if reason is not None:
            continue
        bids = asks = None
        di = _as_of_idx(depth_ts, ts) if depth_ts else None
        if di is not None and ts - depths[di].recv_ts_ms <= align_ms:
            bids = levels_from_depth_curve(depths[di], side="bid") or None
            asks = levels_from_depth_curve(depths[di], side="ask") or None
        sess = session_kind(
            datetime.fromtimestamp(ts / 1000, tz=UTC), config=metrics_cfg
        ).value
        for direction in DIRECTIONS:
            r = compute_pnl_usd(
                pair_id=pair.id,
                bybit_bid=book.bid_de_multiplied,
                bybit_ask=book.ask_de_multiplied,
                size_usd=size_usd,
                direction=direction,  # type: ignore[arg-type]
                venue="amm",
                config=metrics_cfg,
                amm=amm,
                bybit_bids=bids,
                bybit_asks=asks,
            )
            if not r.fillable or r.pnl_bps is None:
                continue
            out.append(
                EdgeSample(
                    ts_ms=ts,
                    pair_id=pair.id,
                    direction=direction,
                    session=sess,  # type: ignore[arg-type]
                    venue="amm",
                    size_usd=size_usd,
                    edge_bps=r.pnl_bps,
                    pnl_usd=r.pnl_usd,
                )
            )
    return out


def build_rfq_samples(
    *,
    pair: Pair,
    books: Sequence[BybitBookTick],
    rfq_ticks: Sequence[FluxionRfqQuoteTick],
    metrics_cfg: Any,
    gaps: Sequence[GapInterval],
    align_ms: int,
) -> list[EdgeSample]:
    """RFQ samples at poll times; size is poll-native (not forced to $500/$1k)."""
    if not books or not rfq_ticks:
        return []
    book_ts = [b.recv_ts_ms for b in books]
    out: list[EdgeSample] = []
    native_dec = pair.fluxion.native_decimals
    for tick in rfq_ticks:
        ts = tick.poll_ts_ms
        if _in_gap(ts, gaps):
            continue
        poll = rfq_tick_to_poll_quote(tick, native_decimals=native_dec)
        if poll is None:
            continue
        bi = _as_of_idx(book_ts, ts)
        if bi is None:
            continue
        book = books[bi]
        if ts - book.recv_ts_ms > align_ms:
            continue
        # Map RFQ leg → paper direction.
        if poll.fluxion_leg == "buy":
            direction = "buy_fluxion_sell_bybit"
        else:
            direction = "buy_bybit_sell_fluxion"
        r = compute_pnl_usd(
            pair_id=pair.id,
            bybit_bid=book.bid_de_multiplied,
            bybit_ask=book.ask_de_multiplied,
            size_usd=Decimal(1),  # ignored for RFQ path
            direction=direction,  # type: ignore[arg-type]
            venue="rfq",
            config=metrics_cfg,
            rfq=poll,
        )
        if not r.fillable or r.pnl_bps is None or r.size_usd <= 0:
            continue
        # Cap reporting notional note: still record actual poll size.
        if r.size_usd > MAX_TRADE_USD:
            # Scale PnL linearly for single-flight cap (conservative).
            scale = MAX_TRADE_USD / r.size_usd
            pnl = r.pnl_usd * scale
            size = MAX_TRADE_USD
            bps = pnl / size * Decimal(10_000)
        else:
            pnl = r.pnl_usd
            size = r.size_usd
            bps = r.pnl_bps
        sess = session_kind(
            datetime.fromtimestamp(ts / 1000, tz=UTC), config=metrics_cfg
        ).value
        out.append(
            EdgeSample(
                ts_ms=ts,
                pair_id=pair.id,
                direction=direction,
                session=sess,  # type: ignore[arg-type]
                venue="rfq",
                size_usd=size,
                edge_bps=bps,
                pnl_usd=pnl,
            )
        )
    return out


def group_key(s: EdgeSample) -> tuple[str, str, str, str, str]:
    # RFQ polls have heterogeneous notionals — bucket under one label so we
    # do not explode into one series per poll size (and a multi‑MB JSON).
    size_label = "poll_native" if s.venue == "rfq" else str(int(s.size_usd))
    return (s.pair_id, s.direction, s.session, s.venue, size_label)


def analyze_series(
    samples: list[EdgeSample],
    *,
    span_ms: int,
    reentry_cooldown_ms: int,
    trade_duration_ms: int,
    max_gap_ms: int,
) -> dict[str, Any]:
    samples = sorted(samples, key=lambda s: s.ts_ms)
    empty_fit = ThresholdFit(
        min_edge_bps=Decimal(0),
        capture_fraction=Decimal("0.70"),
        profit_at_fit=Decimal(0),
        profit_at_zero=Decimal(0),
        windows_per_day=0.0,
        profit_per_day=Decimal(0),
        fitted=False,
    )
    if not samples:
        return {
            "n_samples": 0,
            "sweep": [],
            "fit": _fit_dict(empty_fit),
            "windows_at_zero": 0,
            "profit_at_zero": "0",
            "profit_per_day_at_zero": "0",
        }
    sweep = threshold_sweep(
        samples,
        thresholds_bps=THRESHOLD_BPS,
        max_gap_ms=max_gap_ms,
        reentry_cooldown_ms=reentry_cooldown_ms,
        trade_duration_ms=trade_duration_ms,
        span_ms=span_ms,
        max_trade_usd=MAX_TRADE_USD,
    )
    fit = fit_min_edge_bps(sweep)
    wins0 = detect_windows(
        samples, min_edge_bps=Decimal(0), max_gap_ms=max_gap_ms
    )
    return {
        "n_samples": len(samples),
        "sweep": [_sweep_dict(r) for r in sweep],
        "fit": _fit_dict(fit),
        "windows_at_zero": len(wins0),
        "profit_at_zero": _fmt_dec(sweep[0].capturable_profit_usd) if sweep else "0",
        "profit_per_day_at_zero": (
            _fmt_dec(sweep[0].capturable_profit_per_day) if sweep else "0"
        ),
    }


def _fmt_dec(v: Decimal | str | int | float, places: int = 4) -> str:
    d = Decimal(str(v))
    if d == 0:
        return "0"
    q = Decimal(10) ** -places
    return format(d.quantize(q), "f")


def _bps_label(v: Decimal) -> str:
    return str(int(v)) if v == int(v) else str(v)


def _sweep_dict(r: SweepRow) -> dict[str, Any]:
    return {
        "min_edge_bps": _bps_label(r.min_edge_bps),
        "n_windows": r.n_windows,
        "windows_per_day": round(r.windows_per_day, 3),
        "duration_median_ms": r.duration_median_ms,
        "duration_p90_ms": r.duration_p90_ms,
        "capturable_profit_usd": _fmt_dec(r.capturable_profit_usd),
        "capturable_profit_per_day": _fmt_dec(r.capturable_profit_per_day),
    }


def _fit_dict(f: ThresholdFit) -> dict[str, Any]:
    return {
        "min_edge_bps": _bps_label(f.min_edge_bps),
        "capture_fraction": str(f.capture_fraction),
        "profit_at_fit": _fmt_dec(f.profit_at_fit),
        "profit_at_zero": _fmt_dec(f.profit_at_zero),
        "windows_per_day": round(f.windows_per_day, 3),
        "profit_per_day": _fmt_dec(f.profit_per_day),
        "fitted": f.fitted,
    }


def render_report(payload: dict[str, Any]) -> str:
    gen = payload["generated_utc"]
    lines: list[str] = []
    a = lines.append
    a("# xStocks arb edge quantification & threshold fit (WHI-866 / M8)")
    a("")
    a(
        "Go/no-go report for the sibling execution project "
        "`mantle-stocks-arbitrage-bots`. Headline numbers are **AMM-only** "
        "(bot v1); RFQ is reported separately as a v2 candidate."
    )
    a("")
    a(f"**Generated:** {gen}")
    a("")
    a("## Regeneration")
    a("")
    a("```bash")
    a(payload["repro_command"])
    a("```")
    a("")
    a("Pure helpers: `monitor.analysis.edge_quant` (unit-tested).")
    a("Venue math: `monitor.metrics.pnl_v2.compute_pnl_usd` (not reimplemented).")
    a("")
    a("## Decision rule (bot DESIGN §1.4)")
    a("")
    a("After all costs (Bybit taker 10 bps + Fluxion pool fee + bilateral slip + gas):")
    a("")
    a(
        f"- **Go:** average capturable profit ≥ **{GO_USDT_PER_DAY} USDT/day** "
        f"at {INVENTORY_USD} USDT inventory with ≤ **{MAX_TRADE_USD} USDT** per trade, "
        f"**and** ≥ **{GO_MIN_SYMBOLS} symbols** with stable windows."
    )
    a(f"- **No-go:** < **{NOGO_USDT_PER_DAY} USDT/day**.")
    a(
        "- In between: judge on window time-distribution "
        "(open-auction-only clusters discount heavily)."
    )
    a("")
    a("## Method")
    a("")
    m = payload["method"]
    a("| Item | Value |")
    a("|------|--------|")
    for k, v in m.items():
        a(f"| {k} | {v} |")
    a("")
    a("### Capturable-profit model")
    a("")
    a(
        "- **Single-flight:** at most one trade in flight (series-level for "
        "per-symbol tables; portfolio-level for the go/no-go headline)."
    )
    a(
        f"- **Per-trade cap:** ≤ {MAX_TRADE_USD} USDT (bot max). Analysis rungs: "
        f"$500 and $1,000."
    )
    a(
        f"- **Re-entry cooldown:** {m['reentry_cooldown_ms']} ms after "
        f"trade_duration={m['trade_duration_ms']} ms. Default is one calendar "
        "day so the headline is **one trade per window** (fire-on-open PnL). "
        "Pass a shorter `--reentry-cooldown-ms` for a multi-entry sensitivity."
    )
    a(
        "- **Threshold fit:** highest `min_edge_bps` on the 0..60 / step-2 sweep "
        "that still retains ≥ 70% of zero-threshold capturable profit (knee)."
    )
    a(
        "- **AMM vs RFQ:** separate columns; RFQ sizes are poll-native "
        "(scaled down only when poll notional > $1,000). No AMM impact curve "
        "is interpolated onto RFQ rows."
    )
    a("")
    a("## Data span inventory")
    a("")
    a("### Journal tables used")
    a("")
    a(
        "Retention (`config/collector.yaml` → `retention`) prunes raw Bybit L1 "
        "at ~2 days and depth at ~2 days; pool state ~7 days; RFQ polls ~3 days; "
        "`bybit_book_1m` downsample ~14 days. **This study uses raw aligned "
        "ticks only** (depth-aware PnL v2) — not the 1m bars — because "
        "capturable profit at $500/$1,000 requires the depth curve and pool "
        "geometry, not L1 alone."
    )
    a("")
    a("| Table | Rows | Min (UTC) | Max (UTC) | Role |")
    a("|-------|-----:|-----------|-----------|------|")
    for t in payload["tables"]:
        a(
            f"| `{t['table']}` | {t['n_rows']:,} | {t['min']} | {t['max']} | {t['role']} |"
        )
    a("")
    a("### Per-symbol coverage (raw `bybit_book`)")
    a("")
    a(
        f"Study window wall clock: **{payload['study']['start']} → "
        f"{payload['study']['end']}** "
        f"({payload['study']['span_hours']:.1f} h). "
        f"RTH hours in window: **{payload['study']['rth_hours']:.2f} h** "
        f"(excl. collector_down: **{payload['study']['rth_hours_excl_gap']:.2f} h**). "
        f"Closed hours: **{payload['study']['closed_hours']:.2f} h** "
        f"(excl. gap: **{payload['study']['closed_hours_excl_gap']:.2f} h**)."
    )
    a("")
    a(
        f"**Collector downtime:** {payload['study']['gap_count']} "
        f"`collector_down` intervals totaling "
        f"**{payload['study']['gap_hours']:.2f} h** "
        f"(of which **{payload['study']['gap_rth_hours']:.2f} h** fell inside RTH). "
        "Samples inside those intervals are excluded."
    )
    a("")
    a("| Pair | Book rows | First | Last | AMM pool? | Notes |")
    a("|------|----------:|-------|------|-----------|-------|")
    for row in payload["pairs_span"]:
        a(
            f"| {row['pair_id']} | {row['n']:,} | {row['min']} | {row['max']} | "
            f"{'yes' if row['has_amm'] else 'no'} | {row['notes']} |"
        )
    a("")
    if payload["study"]["rth_hours_excl_gap"] < 6:
        a(
            "> **Coverage caveat:** effective RTH after downtime is under 6 hours. "
            "Fit quality is **low** — treat thresholds as provisional and re-run "
            "after ≥5 clean RTH sessions."
        )
        a("")
    a("## Hygiene / exclusions")
    a("")
    for line in payload["hygiene"]:
        a(f"- {line}")
    a("")
    a("## Headline go/no-go (AMM-only, portfolio single-flight)")
    a("")
    h = payload["headline"]
    a("| Metric | Value |")
    a("|--------|------:|")
    a(f"| Study calendar days (span/86400s) | {h['calendar_days']:.3f} |")
    a(f"| Portfolio capturable profit (total, $1000 rung) | {h['profit_total_usd']} USDT |")
    a(f"| **Average capturable profit / day** | **{h['profit_per_day_usd']} USDT/day** |")
    a(f"| Symbols with fitted stable windows (open, $1000) | {h['n_stable_symbols']} |")
    a(f"| Go-list | {', '.join(h['go_list']) if h['go_list'] else '—'} |")
    core = ", ".join(h["core_go_list"]) if h["core_go_list"] else "—"
    a(f"| Core go-list (≥1 USDT/day open fit) | {core} |")
    a(f"| Portfolio $/day excluding HOODx | {h['profit_per_day_ex_hoodx']} |")
    a(f"| **Verdict** | **{h['verdict']}** |")
    a("")
    a(h["verdict_detail"])
    a("")
    if h.get("concentration_note"):
        a(h["concentration_note"])
        a("")
    a("## Fitted `min_edge_bps` (AMM, for bot config)")
    a("")
    a(
        "Per (symbol × session) at the **$1,000** rung, best direction by "
        "zero-threshold profit/day. Knee fit = highest threshold retaining ≥70% "
        "of that direction's capturable profit (windows filtered from the T=0 "
        "set by ``peak_edge_bps``). Rows marked **ceiling** hit the top of the "
        "0..60 sweep still above the capture bar — treat 60 as a lower bound, "
        "not a tight optimum."
    )
    a("")
    a(
        "| Symbol | Session | Direction | Fit bps | Flag | Windows/day | "
        "Profit/day (USDT) | Fitted? | N samples |"
    )
    a(
        "|--------|---------|-----------|--------:|------|------------:|"
        "------------------:|---------|----------:|"
    )
    for row in payload["fits_amm_1000"]:
        a(
            f"| {row['pair_id']} | {row['session']} | {row['direction']} | "
            f"{row['min_edge_bps']} | {row.get('flag', '—')} | "
            f"{row['windows_per_day']:.2f} | "
            f"{row['profit_per_day']} | {row['fitted']} | {row['n_samples']} |"
        )
    a("")
    a("## Window statistics (selected thresholds)")
    a("")
    a(
        "Selected thresholds 0 / 10 / 20 / 40 bps are tabulated below; the "
        "companion JSON keeps the same selected rows per series (re-run the "
        "script to rebuild the full 0..60 / step-2 sweep in memory)."
    )
    a("")
    a("### AMM $1,000 — RTH open")
    a("")
    a(
        "| Symbol | Dir | T=0 w/d | T=0 $/d | T=10 w/d | T=10 $/d | "
        "T=20 w/d | T=20 $/d | T=40 w/d | T=40 $/d |"
    )
    a(
        "|--------|-----|--------:|--------:|---------:|---------:|"
        "---------:|---------:|---------:|---------:|"
    )
    for row in payload["amm_open_1000_table"]:
        a(
            f"| {row['pair_id']} | {row['direction'][:16]} | "
            f"{row['t0_wd']:.2f} | {row['t0_pd']} | "
            f"{row['t10_wd']:.2f} | {row['t10_pd']} | "
            f"{row['t20_wd']:.2f} | {row['t20_pd']} | "
            f"{row['t40_wd']:.2f} | {row['t40_pd']} |"
        )
    a("")
    a("### AMM $1,000 — closed session")
    a("")
    a(
        "| Symbol | Dir | T=0 w/d | T=0 $/d | T=10 w/d | T=10 $/d | "
        "T=20 w/d | T=20 $/d | T=40 w/d | T=40 $/d |"
    )
    a(
        "|--------|-----|--------:|--------:|---------:|---------:|"
        "---------:|---------:|---------:|---------:|"
    )
    for row in payload["amm_closed_1000_table"]:
        a(
            f"| {row['pair_id']} | {row['direction'][:16]} | "
            f"{row['t0_wd']:.2f} | {row['t0_pd']} | "
            f"{row['t10_wd']:.2f} | {row['t10_pd']} | "
            f"{row['t20_wd']:.2f} | {row['t20_pd']} | "
            f"{row['t40_wd']:.2f} | {row['t40_pd']} |"
        )
    a("")
    a("### AMM $500 — RTH open (summary profit/day at T=0)")
    a("")
    a("| Symbol | Dir | Windows/day | Profit/day (USDT) | N samples |")
    a("|--------|-----|------------:|------------------:|----------:|")
    for row in payload["amm_open_500_summary"]:
        a(
            f"| {row['pair_id']} | {row['direction'][:16]} | "
            f"{row['windows_per_day']:.2f} | {row['profit_per_day']} | "
            f"{row['n_samples']} |"
        )
    a("")
    a("### RFQ — RTH open (poll-native size, capped at $1,000)")
    a("")
    a(
        "| Symbol | Dir | T=0 w/d | T=0 $/d | T=10 w/d | T=10 $/d | "
        "Fit bps | Fitted? |"
    )
    a(
        "|--------|-----|--------:|--------:|---------:|---------:|"
        "--------:|---------|"
    )
    for row in payload["rfq_open_table"]:
        a(
            f"| {row['pair_id']} | {row['direction'][:16]} | "
            f"{row['t0_wd']:.2f} | {row['t0_pd']} | "
            f"{row['t10_wd']:.2f} | {row['t10_pd']} | "
            f"{row['fit_bps']} | {row['fitted']} |"
        )
    a("")
    a("## Closed-session note")
    a("")
    a(payload["closed_note"])
    a("")
    a("## RFQ vs AMM (v2 signal)")
    a("")
    a(payload["rfq_vs_amm_note"])
    a("")
    a("## Fit quality & next steps")
    a("")
    for line in payload["next_steps"]:
        a(f"- {line}")
    a("")
    a("---")
    a("")
    a(
        f"*Companion machine-readable payload: "
        f"`docs/references/m8-xstocks-edge-quant.json` "
        f"(schema version {payload['schema_version']}).*"
    )
    a("")
    return "\n".join(lines)


def _pick_sweep(sweep: list[dict[str, Any]], thr: str) -> dict[str, Any] | None:
    for row in sweep:
        if Decimal(row["min_edge_bps"]) == Decimal(thr):
            return row
    return None


def run(args: argparse.Namespace) -> int:
    t0 = time.time()
    db_path = Path(args.db).resolve()
    if not db_path.is_file():
        print(f"journal not found: {db_path}", file=sys.stderr)
        return 2

    ctx = load_market_context("bybit-fluxion", load_collector=False, repo_root=_REPO)
    assert ctx.pairs is not None
    metrics_cfg = ctx.metrics
    pairs_amm = ctx.pairs.pairs_with_amm()
    pairs_all = {p.id: p for p in ctx.pairs.pairs}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    gaps = load_gaps(conn)
    spans = {
        "bybit_book": table_span(conn, "bybit_book", "recv_ts_ms"),
        "bybit_depth": table_span(conn, "bybit_depth", "recv_ts_ms"),
        "fluxion_pool_state": table_span(conn, "fluxion_pool_state", "recv_ts_ms"),
        "fluxion_rfq_quotes": table_span(conn, "fluxion_rfq_quotes", "poll_ts_ms"),
        "bybit_book_1m": table_span(conn, "bybit_book_1m", "bucket_ts_ms"),
        "collector_gaps": table_span(conn, "collector_gaps", "gap_start_ms"),
    }
    book_span = spans["bybit_book"]
    if book_span.min_ms is None or book_span.max_ms is None:
        print("bybit_book is empty — cannot run study", file=sys.stderr)
        return 2

    since_ms = book_span.min_ms
    until_ms = book_span.max_ms
    span_ms = max(1, until_ms - since_ms)
    calendar_days = Decimal(span_ms) / Decimal(86_400_000)

    rth_h, closed_h, rth_ok, closed_ok = rth_closed_hours(
        since_ms, until_ms, metrics_cfg=metrics_cfg, gaps=gaps
    )
    gap_hours = sum((g.end_ms - g.start_ms) for g in gaps) / 3_600_000
    # RTH lost already computed as rth_h - rth_ok
    gap_rth = rth_h - rth_ok

    pair_rows = per_pair_book_span(conn)
    pairs_span = []
    for pid, n, mn, mx in pair_rows:
        p = pairs_all.get(pid)
        has_amm = bool(p and p.fluxion.amm is not None)
        notes = []
        if not has_amm:
            notes.append("CEX-only (no Fluxion pool)")
        if pid == "SPCXx":
            notes.append("frequent pricing_anomaly / thin pool — often excluded by gate")
        pairs_span.append(
            {
                "pair_id": pid,
                "n": n,
                "min": _ms_iso(mn),
                "max": _ms_iso(mx),
                "has_amm": has_amm,
                "notes": "; ".join(notes) if notes else "—",
            }
        )

    # --- Build samples ---
    series: dict[tuple[str, str, str, str, str], list[EdgeSample]] = defaultdict(list)
    sample_ms = int(args.sample_ms)
    align_ms = int(args.align_ms)
    reentry = int(args.reentry_cooldown_ms)
    trade_dur = int(args.trade_duration_ms)
    max_gap = int(args.max_gap_ms)

    print(
        f"study window {_ms_iso(since_ms)} → {_ms_iso(until_ms)} "
        f"({span_ms/3.6e6:.1f}h), sample={sample_ms}ms, {len(pairs_amm)} AMM pairs",
        flush=True,
    )

    for pair in pairs_amm:
        print(f"  loading {pair.id}…", flush=True)
        books = load_bucketed_books(
            conn, pair.id, sample_ms=sample_ms, since_ms=since_ms, until_ms=until_ms
        )
        pools = load_pools(conn, pair.id, since_ms=since_ms - align_ms, until_ms=until_ms)
        depths = load_depths(
            conn, pair.id, since_ms=since_ms - align_ms, until_ms=until_ms
        )
        rfq_ticks = load_rfq(
            conn, pair.id, since_ms=since_ms, until_ms=until_ms
        )
        print(
            f"    books={len(books)} pools={len(pools)} depth={len(depths)} rfq={len(rfq_ticks)}",
            flush=True,
        )
        for size in NOTIONALS:
            samples = build_amm_samples(
                pair=pair,
                books=books,
                pools=pools,
                depths=depths,
                metrics_cfg=metrics_cfg,
                quote_decimals=ctx.dex.quote_decimals,
                size_usd=size,
                gaps=gaps,
                align_ms=align_ms,
                max_abs_spread_bps=metrics_cfg.max_abs_amm_spread_bps,
            )
            for s in samples:
                series[group_key(s)].append(s)
            print(f"    AMM ${size}: {len(samples)} fillable samples", flush=True)

        rfq_samples = build_rfq_samples(
            pair=pair,
            books=books,
            rfq_ticks=rfq_ticks,
            metrics_cfg=metrics_cfg,
            gaps=gaps,
            align_ms=align_ms,
        )
        for s in rfq_samples:
            series[group_key(s)].append(s)
        print(f"    RFQ: {len(rfq_samples)} fillable samples", flush=True)

    conn.close()

    # --- Analyze each series ---
    results: dict[str, Any] = {}
    amm_open_1000_table: list[dict[str, Any]] = []
    amm_closed_1000_table: list[dict[str, Any]] = []
    amm_open_500_summary: list[dict[str, Any]] = []
    rfq_open_table: list[dict[str, Any]] = []
    all_amm_1000: list[OpportunityWindow] = []

    def _amm_threshold_row(
        pair_id: str, direction: str, analysis: dict[str, Any]
    ) -> dict[str, Any]:
        s0 = _pick_sweep(analysis["sweep"], "0")
        s10 = _pick_sweep(analysis["sweep"], "10")
        s20 = _pick_sweep(analysis["sweep"], "20")
        s40 = _pick_sweep(analysis["sweep"], "40")
        return {
            "pair_id": pair_id,
            "direction": direction,
            "t0_wd": s0["windows_per_day"] if s0 else 0,
            "t0_pd": s0["capturable_profit_per_day"] if s0 else "0",
            "t10_wd": s10["windows_per_day"] if s10 else 0,
            "t10_pd": s10["capturable_profit_per_day"] if s10 else "0",
            "t20_wd": s20["windows_per_day"] if s20 else 0,
            "t20_pd": s20["capturable_profit_per_day"] if s20 else "0",
            "t40_wd": s40["windows_per_day"] if s40 else 0,
            "t40_pd": s40["capturable_profit_per_day"] if s40 else "0",
        }

    for key, samples in sorted(series.items()):
        pair_id, direction, session, venue, size_s = key
        analysis = analyze_series(
            samples,
            span_ms=span_ms,
            reentry_cooldown_ms=reentry,
            trade_duration_ms=trade_dur,
            max_gap_ms=max_gap,
        )
        results["|".join(key)] = analysis

        if venue == "amm" and size_s == "1000":
            wins = detect_windows(
                sorted(samples, key=lambda s: s.ts_ms),
                min_edge_bps=Decimal(0),
                max_gap_ms=max_gap,
            )
            all_amm_1000.extend(wins)
            row = _amm_threshold_row(pair_id, direction, analysis)
            if session == "open":
                amm_open_1000_table.append(row)
            else:
                amm_closed_1000_table.append(row)

        if venue == "amm" and size_s == "500" and session == "open":
            s0 = _pick_sweep(analysis["sweep"], "0")
            amm_open_500_summary.append(
                {
                    "pair_id": pair_id,
                    "direction": direction,
                    "windows_per_day": s0["windows_per_day"] if s0 else 0,
                    "profit_per_day": s0["capturable_profit_per_day"] if s0 else "0",
                    "n_samples": analysis["n_samples"],
                }
            )

        if venue == "rfq" and session == "open":
            s0 = _pick_sweep(analysis["sweep"], "0")
            s10 = _pick_sweep(analysis["sweep"], "10")
            fit = analysis["fit"]
            rfq_open_table.append(
                {
                    "pair_id": pair_id,
                    "direction": direction,
                    "t0_wd": s0["windows_per_day"] if s0 else 0,
                    "t0_pd": s0["capturable_profit_per_day"] if s0 else "0",
                    "t10_wd": s10["windows_per_day"] if s10 else 0,
                    "t10_pd": s10["capturable_profit_per_day"] if s10 else "0",
                    "fit_bps": fit["min_edge_bps"],
                    "fitted": fit["fitted"],
                }
            )

    # Best direction per (pair, session) for fit table at AMM $1000.
    best_open: dict[str, dict[str, Any]] = {}
    best_closed: dict[str, dict[str, Any]] = {}
    for key, analysis in results.items():
        pair_id, direction, session, venue, size_s = key.split("|")
        if venue != "amm" or size_s != "1000":
            continue
        bucket = best_open if session == "open" else best_closed
        profit = Decimal(analysis["profit_at_zero"] or "0")
        prev = bucket.get(pair_id)
        if prev is None or profit > Decimal(prev["profit_at_zero"]):
            bucket[pair_id] = {
                "pair_id": pair_id,
                "session": session,
                "direction": direction,
                "min_edge_bps": analysis["fit"]["min_edge_bps"],
                "windows_per_day": analysis["fit"]["windows_per_day"],
                "profit_per_day": analysis["fit"]["profit_per_day"],
                "fitted": analysis["fit"]["fitted"],
                "n_samples": analysis["n_samples"],
                "profit_at_zero": analysis["profit_at_zero"],
            }
    fits_amm_1000 = sorted(
        list(best_open.values()) + list(best_closed.values()),
        key=lambda r: (r["pair_id"], 0 if r["session"] == "open" else 1),
    )

    # Portfolio single-flight: AMM $1000, open+closed, T=0 windows,
    # one entry per window (default reentry cooldown).
    port_profit = portfolio_capturable_profit(
        all_amm_1000,
        reentry_cooldown_ms=reentry,
        trade_duration_ms=trade_dur,
        max_trade_usd=MAX_TRADE_USD,
        inventory_usd=INVENTORY_USD,
    )
    profit_per_day = (
        port_profit / calendar_days if calendar_days > 0 else Decimal(0)
    )

    # Annotate ceiling-pinned fits (hit top of 0..60 grid still ≥70% of P0).
    for bucket in (best_open, best_closed):
        for row in bucket.values():
            row["flag"] = (
                "ceiling"
                if row["fitted"] and Decimal(row["min_edge_bps"]) >= Decimal(60)
                else "—"
            )

    # Stable symbols: open-session fitted with profit/day > 0 at $1000.
    go_list = sorted(
        {
            r["pair_id"]
            for r in best_open.values()
            if r["fitted"] and Decimal(r["profit_per_day"]) > 0
        }
    )
    core_go_list = sorted(
        {
            r["pair_id"]
            for r in best_open.values()
            if r["fitted"] and Decimal(r["profit_per_day"]) >= Decimal(1)
        }
    )
    n_stable = len(go_list)

    # HOODx concentration sensitivity (portfolio recompute excluding HOODx).
    port_ex_hood = portfolio_capturable_profit(
        [w for w in all_amm_1000 if w.pair_id != "HOODx"],
        reentry_cooldown_ms=reentry,
        trade_duration_ms=trade_dur,
        max_trade_usd=MAX_TRADE_USD,
        inventory_usd=INVENTORY_USD,
    )
    profit_ex_hood_day = (
        port_ex_hood / calendar_days if calendar_days > 0 else Decimal(0)
    )
    hood_open = best_open.get("HOODx")
    concentration_note = ""
    if hood_open and Decimal(hood_open["profit_per_day"]) > 0:
        concentration_note = (
            f"**Concentration:** HOODx open fit contributes "
            f"{hood_open['profit_per_day']} USDT/day of series-level profit; "
            f"portfolio single-flight excluding all HOODx windows is "
            f"**{profit_ex_hood_day:.2f} USDT/day** "
            f"({'still ≥15' if profit_ex_hood_day >= GO_USDT_PER_DAY else 'below 15'}). "
            f"Core go-list (≥1 USDT/day open fit): "
            f"{', '.join(core_go_list) if core_go_list else '—'}. "
            f"GOOGLx/TSLAx seats are thin — provisional only."
        )

    if profit_per_day >= GO_USDT_PER_DAY and n_stable >= GO_MIN_SYMBOLS:
        verdict = "GO"
        detail = (
            f"Portfolio average **{profit_per_day:.2f} USDT/day** meets the "
            f"≥{GO_USDT_PER_DAY} gate with **{n_stable}** symbols showing "
            f"positive fitted open-session windows ({', '.join(go_list)}). "
            f"Core (≥1 USDT/day): {', '.join(core_go_list) if core_go_list else '—'}."
        )
    elif profit_per_day < NOGO_USDT_PER_DAY:
        verdict = "NO-GO"
        detail = (
            f"Portfolio average **{profit_per_day:.2f} USDT/day** is below the "
            f"{NOGO_USDT_PER_DAY} USDT/day no-go floor. Do not write execution "
            "code beyond the bot repo's M1 foundation until a longer clean RTH "
            "span reverses this."
        )
    else:
        verdict = "BORDERLINE"
        detail = (
            f"Portfolio average **{profit_per_day:.2f} USDT/day** sits between "
            f"{NOGO_USDT_PER_DAY} and {GO_USDT_PER_DAY}. Open-session stable "
            f"symbols: {n_stable} (need ≥{GO_MIN_SYMBOLS}). Inspect time "
            "distribution — auction-only clusters discount heavily."
        )

    # Closed-session note
    closed_profit = Decimal(0)
    for key, analysis in results.items():
        _p, _d, session, venue, size_s = key.split("|")
        if venue == "amm" and size_s == "1000" and session == "closed":
            closed_profit += Decimal(analysis["profit_at_zero"] or "0")
    closed_note = (
        f"AMM $1000 closed-session capturable profit (sum of per-series "
        f"single-flight, **not** portfolio-deconflicted): "
        f"{closed_profit:.2f} USDT over the span "
        f"({(closed_profit / calendar_days) if calendar_days else 0:.2f}/day). "
        "Closed-session RFQ remains two-sided on liquid pairs "
        "(see `docs/references/m4-closed-session-rfq.md`); session ≠ mechanism."
    )

    rfq_profit = Decimal(0)
    for key, analysis in results.items():
        _p, _d, session, venue, _sz = key.split("|")
        if venue == "rfq" and session == "open":
            rfq_profit += Decimal(analysis["profit_at_zero"] or "0")
    rfq_vs_amm = (
        f"RFQ open-session sum of per-series capturable profit at T=0: "
        f"{rfq_profit:.2f} USDT "
        f"({(rfq_profit / calendar_days) if calendar_days else 0:.2f}/day). "
        "This is **not** portfolio-deconflicted against AMM and uses poll "
        "quotes that are vendor-asserted but not fill-verified — RFQ stays "
        "v2 until a fill-firmness test lands. Headline go/no-go ignores RFQ."
    )

    elapsed = time.time() - t0
    try:
        db_disp = db_path.resolve().relative_to(_REPO.resolve())
    except ValueError:
        # Symlinked journals (worktree → primary data/) still print a stable default.
        db_disp = Path("data/monitor-bybit-fluxion.db")
    repro = (
        "uv run python scripts/xstocks_edge_quant.py "
        f"--db {db_disp} --sample-ms {sample_ms}"
    )

    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_utc": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "repro_command": repro,
        "elapsed_s": round(elapsed, 1),
        "method": {
            "market": "bybit-fluxion",
            "engine": "monitor.metrics.pnl_v2.compute_pnl_usd",
            "analysis": "monitor.analysis.edge_quant",
            "notionals_usd": "500, 1000",
            "sample_ms": sample_ms,
            "align_ms": align_ms,
            "reentry_cooldown_ms": reentry,
            "trade_duration_ms": trade_dur,
            "max_gap_ms": max_gap,
            "threshold_sweep_bps": "0..60 step 2",
            "capture_fraction": "0.70",
            "inventory_usd": str(INVENTORY_USD),
            "max_trade_usd": str(MAX_TRADE_USD),
            "pricing_anomaly_gate": str(metrics_cfg.max_abs_amm_spread_bps),
        },
        "tables": [
            {
                "table": "bybit_book",
                "n_rows": spans["bybit_book"].n_rows,
                "min": _ms_iso(spans["bybit_book"].min_ms),
                "max": _ms_iso(spans["bybit_book"].max_ms),
                "role": "CEX L1 (primary timeline; ~2d raw retention)",
            },
            {
                "table": "bybit_depth",
                "n_rows": spans["bybit_depth"].n_rows,
                "min": _ms_iso(spans["bybit_depth"].min_ms),
                "max": _ms_iso(spans["bybit_depth"].max_ms),
                "role": "CEX VWAP curve for $500/$1k slip (~2d retention)",
            },
            {
                "table": "fluxion_pool_state",
                "n_rows": spans["fluxion_pool_state"].n_rows,
                "min": _ms_iso(spans["fluxion_pool_state"].min_ms),
                "max": _ms_iso(spans["fluxion_pool_state"].max_ms),
                "role": "AMM geometry (as-of join; ~7d retention)",
            },
            {
                "table": "fluxion_rfq_quotes",
                "n_rows": spans["fluxion_rfq_quotes"].n_rows,
                "min": _ms_iso(spans["fluxion_rfq_quotes"].min_ms),
                "max": _ms_iso(spans["fluxion_rfq_quotes"].max_ms),
                "role": "RFQ polls (separate column; ~3d retention)",
            },
            {
                "table": "bybit_book_1m",
                "n_rows": spans["bybit_book_1m"].n_rows,
                "min": _ms_iso(spans["bybit_book_1m"].min_ms),
                "max": _ms_iso(spans["bybit_book_1m"].max_ms),
                "role": "Survives longer (~14d) but **not used** (no depth)",
            },
            {
                "table": "collector_gaps",
                "n_rows": spans["collector_gaps"].n_rows,
                "min": _ms_iso(spans["collector_gaps"].min_ms),
                "max": _ms_iso(spans["collector_gaps"].max_ms),
                "role": "Downtime exclusion (`source=collector_down`)",
            },
        ],
        "study": {
            "start": _ms_iso(since_ms),
            "end": _ms_iso(until_ms),
            "span_hours": span_ms / 3_600_000,
            "rth_hours": rth_h,
            "closed_hours": closed_h,
            "rth_hours_excl_gap": rth_ok,
            "closed_hours_excl_gap": closed_ok,
            "gap_count": len(gaps),
            "gap_hours": gap_hours,
            "gap_rth_hours": gap_rth,
        },
        "pairs_span": pairs_span,
        "hygiene": [
            "No automated corporate-action calendar is applied. The observation "
            f"window is {_ms_iso(since_ms)} → {_ms_iso(until_ms)}; re-runs over "
            "a longer span must re-check dividends/splits/rebases and disclose "
            "any excluded days (bot DESIGN §8).",
            "No bStocks-style share rebase segment break was introduced for "
            "Fluxion xStocks wrappers in this study.",
            "Samples with `gap=1` on book/pool/depth rows are dropped at load.",
            "Pairs failing `amm_quote_for_cex` (empty_pool / invalid_mid / "
            "pricing_anomaly, default |spread| > 500 bps) are excluded from "
            "AMM samples for that timestamp (SPCXx frequently hits this).",
            "CEX-only inventory pairs (no Fluxion AMM) are out of scope for "
            "the bot v1 AMM path and appear only in the coverage table.",
            "Headline portfolio mixes open+closed AMM $1000 windows at T=0 "
            "(one trade per window, single-flight). Go-list symbols are "
            "open-session fits only.",
        ],
        "headline": {
            "calendar_days": float(calendar_days),
            "profit_total_usd": f"{port_profit:.4f}",
            "profit_per_day_usd": f"{profit_per_day:.4f}",
            "profit_per_day_ex_hoodx": f"{profit_ex_hood_day:.4f}",
            "n_stable_symbols": n_stable,
            "go_list": go_list,
            "core_go_list": core_go_list,
            "verdict": verdict,
            "verdict_detail": detail,
            "concentration_note": concentration_note,
        },
        "fits_amm_1000": fits_amm_1000,
        "amm_open_1000_table": sorted(
            amm_open_1000_table, key=lambda r: (r["pair_id"], r["direction"])
        ),
        "amm_closed_1000_table": sorted(
            amm_closed_1000_table, key=lambda r: (r["pair_id"], r["direction"])
        ),
        "amm_open_500_summary": sorted(
            amm_open_500_summary, key=lambda r: (r["pair_id"], r["direction"])
        ),
        "rfq_open_table": sorted(
            rfq_open_table, key=lambda r: (r["pair_id"], r["direction"])
        ),
        "closed_note": closed_note,
        "rfq_vs_amm_note": rfq_vs_amm,
        "next_steps": [
            "Re-run after ≥5 consecutive clean RTH sessions with "
            "`collector_down` RTH loss < 1 h/day — current fit quality is "
            "limited by downtime inside the 2-day raw retention window.",
            "Wire the fit table into the bot repo M4 threshold config "
            "(WHI-876); start with CRCLx / HOODx / NVDAx (material open-session "
            "profit/day) and treat GOOGLx / TSLAx as optional add-ons.",
            "Manually review large HOODx dislocations (gross basis near the "
            "500 bps pricing_anomaly gate) on a fill before sizing up — paper "
            "fillable ≠ firm when AMM mid is slow to update.",
            "RFQ fill-firmness remains a separate gate before any RFQ leg.",
        ],
        # Compact series: drop full 0..60 sweeps (regenerate with script for
        # those); keep fit + T=0/10/20/40 summary so the JSON stays reviewable.
        "series": {
            k: {
                "n_samples": v["n_samples"],
                "windows_at_zero": v["windows_at_zero"],
                "profit_at_zero": v["profit_at_zero"],
                "profit_per_day_at_zero": v["profit_per_day_at_zero"],
                "fit": v["fit"],
                "sweep_selected": [
                    row
                    for row in v["sweep"]
                    if row["min_edge_bps"] in ("0", "10", "20", "40", "60")
                ],
            }
            for k, v in results.items()
        },
    }

    report_path = Path(args.report)
    json_path = Path(args.json_out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    md = render_report(payload)
    report_path.write_text(md, encoding="utf-8")
    # JSON: convert for dump
    json_path.write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(f"\nWrote {report_path}")
    print(f"Wrote {json_path}")
    print(f"Verdict: {verdict}  ({profit_per_day:.2f} USDT/day, {n_stable} symbols)")
    print(f"Elapsed {elapsed:.1f}s")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        default=str(_REPO / "data" / "monitor-bybit-fluxion.db"),
        help="Path to bybit-fluxion journal SQLite",
    )
    p.add_argument(
        "--report",
        default=str(DEFAULT_REPORT),
        help="Markdown report output path",
    )
    p.add_argument(
        "--json-out",
        default=str(DEFAULT_JSON),
        help="Machine-readable companion JSON",
    )
    p.add_argument("--sample-ms", type=int, default=DEFAULT_SAMPLE_MS)
    p.add_argument("--align-ms", type=int, default=DEFAULT_ALIGN_MS)
    p.add_argument(
        "--reentry-cooldown-ms", type=int, default=DEFAULT_REENTRY_COOLDOWN_MS
    )
    p.add_argument(
        "--trade-duration-ms", type=int, default=DEFAULT_TRADE_DURATION_MS
    )
    p.add_argument("--max-gap-ms", type=int, default=DEFAULT_MAX_GAP_MS)
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
