#!/usr/bin/env python3
"""WHI-866 / WHI-909 / M8: xStocks arb edge quantification & threshold fit.

Reads the bybit-fluxion collector journal, drives the PnL v2 cash-flow engine
at $500 / $1,000 notional for both directions under the **corrected cost
stack** (WHI-909):

  * live per-timestamp USDCUSDT premium (not a hardcoded 7.5)
  * Bybit taker sensitivity at 10 and 20 bps (20 = Adventure Zone, believed)
  * rebalance amortization at 1 / 5 / 13.5 bps on skew-building direction
  * extended min_edge_bps sweep 0..200 (step 2 to 60, step 5 beyond)

Writes:

  docs/references/m8-xstocks-edge-quant.md
  docs/references/m8-xstocks-edge-quant.json

Prior WHI-866 report retained as:

  docs/references/m8-xstocks-edge-quant-v1-whi866.md

Usage (repo root; journal must exist + network for USDCUSDT klines unless cached):

  uv run python scripts/xstocks_edge_quant.py
  uv run python scripts/xstocks_edge_quant.py --db data/monitor-bybit-fluxion.db
  uv run python scripts/xstocks_edge_quant.py --sample-ms 20000
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

from monitor.analysis.cost_stack import (  # noqa: E402
    WHI835_LAND_MS,
    downtime_by_day,
    load_or_fetch_basis_series,
)
from monitor.analysis.edge_quant import (  # noqa: E402
    EdgeSample,
    OpportunityWindow,
    SweepRow,
    ThresholdFit,
    apply_rebalance_amortization_many,
    detect_windows,
    extended_threshold_grid_bps,
    fit_min_edge_bps,
    portfolio_capturable_profit,
    threshold_sweep,
)
from monitor.markets import load_market_context  # noqa: E402
from monitor.metrics.capture import (  # noqa: E402
    GapInterval,
    build_amm_samples,
    build_rfq_samples,
)
from monitor.metrics.session import session_kind  # noqa: E402
from monitor.quotes import (  # noqa: E402
    BybitBookTick,
    BybitDepthTick,
    FluxionPoolStateTick,
    FluxionRfqQuoteTick,
)
from monitor.storage import JournalReader  # noqa: E402

# Bot DESIGN §1.4 gates.
GO_USDT_PER_DAY = Decimal("15")
NOGO_USDT_PER_DAY = Decimal("5")
GO_MIN_SYMBOLS = 3
INVENTORY_USD = Decimal("5000")
MAX_TRADE_USD = Decimal("1000")
NOTIONALS = (Decimal("500"), Decimal("1000"))
DIRECTIONS = ("buy_fluxion_sell_bybit", "buy_bybit_sell_fluxion")
# WHI-909: 0..60 step 2, then coarse to 200 so ceiling-flagged fits can bracket.
THRESHOLD_BPS = extended_threshold_grid_bps()
SWEEP_CEILING_BPS = THRESHOLD_BPS[-1]
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
DEFAULT_BASIS_CACHE = (
    _REPO / "docs" / "references" / "m8-xstocks-edge-quant-usdcusdt-klines.json"
)
# Cost-stack sensitivity (WHI-909). Primary = 20 bps taker + 1 bps rebalance.
TAKER_TIERS_BPS = (Decimal(10), Decimal(20))
REBALANCE_TIERS_BPS = (Decimal("1"), Decimal("5"), Decimal("13.5"))
PRIMARY_TAKER_BPS = Decimal(20)
PRIMARY_REBALANCE_BPS = Decimal("1")
SCHEMA_VERSION = 2


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


def load_gaps(reader: JournalReader) -> list[GapInterval]:
    """Collector downtime gaps via public JournalReader (WHI-963)."""
    return [
        GapInterval(start_ms=g.gap_start_ms, end_ms=g.gap_end_ms, source=g.source)
        for g in reader.collector_down_gaps()
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
    reader: JournalReader,
    pair_id: str,
    *,
    sample_ms: int,
    since_ms: int,
    until_ms: int,
) -> list[BybitBookTick]:
    """One book tick per sample_ms bucket (public JournalReader bulk loader)."""
    return reader.bucketed_bybit_books(
        pair_id, sample_ms=sample_ms, since_ms=since_ms, until_ms=until_ms
    )


def load_pools(
    reader: JournalReader,
    pair_id: str,
    *,
    since_ms: int,
    until_ms: int,
) -> list[FluxionPoolStateTick]:
    return reader.pool_states_range(pair_id, since_ms=since_ms, until_ms=until_ms)


def load_depths(
    reader: JournalReader,
    pair_id: str,
    *,
    since_ms: int,
    until_ms: int,
) -> list[BybitDepthTick]:
    return reader.bybit_depths_range(pair_id, since_ms=since_ms, until_ms=until_ms)


def load_rfq(
    reader: JournalReader,
    pair_id: str,
    *,
    since_ms: int,
    until_ms: int,
) -> list[FluxionRfqQuoteTick]:
    return reader.rfq_quotes_range(pair_id, since_ms=since_ms, until_ms=until_ms)


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
    a("# xStocks arb edge quantification & threshold fit (WHI-909 / M8)")
    a("")
    a(
        "Go/no-go report for the sibling execution project "
        "`mantle-stocks-arbitrage-bots` under the **corrected cost stack** "
        "(WHI-909). Headline numbers are **AMM-only** (bot v1); RFQ is "
        "reported separately as a v2 candidate."
    )
    a("")
    a(f"**Generated:** {gen}")
    a("")
    a(
        "> **Supersedes WHI-866.** Prior report retained at "
        "`docs/references/m8-xstocks-edge-quant-v1-whi866.md` (10 bps taker, "
        "0 basis, 0 rebalance, 0..60 sweep). This re-run uses live USDCUSDT "
        "premium, 20 bps Adventure Zone taker (primary), rebalance "
        "amortization, and an extended 0..200 bps threshold sweep."
    )
    a("")
    a("## Regeneration")
    a("")
    a("```bash")
    a(payload["repro_command"])
    a("```")
    a("")
    a(
        "Pure helpers: `monitor.analysis.edge_quant` + "
        "`monitor.analysis.cost_stack` (unit-tested)."
    )
    a("Venue math: `monitor.metrics.pnl_v2.compute_pnl_usd` (not reimplemented).")
    a("")
    a("## Collector health (gate before headlines)")
    a("")
    ch = payload["collector_health"]
    a(
        f"Downtime inventory since WHI-835 land "
        f"({ch['since']}): **{ch['n_gap_intervals']}** `collector_down` "
        f"intervals, **{ch['total_gap_hours']:.2f} h** total wall-clock gap."
    )
    a("")
    a("| Day (UTC) | Total gap (h) | RTH (h) | RTH lost (h) | RTH clean (h) |")
    a("|-----------|-------------:|--------:|-------------:|--------------:|")
    for row in ch["by_day"]:
        a(
            f"| {row['day']} | {row['total_gap_hours']:.2f} | "
            f"{row['rth_hours']:.2f} | {row['rth_gap_hours']:.2f} | "
            f"{row['rth_clean_hours']:.2f} |"
        )
    a("")
    a(
        f"**Study window clean RTH (excl. collector_down):** "
        f"**{payload['study']['rth_hours_excl_gap']:.2f} h** "
        f"(wall RTH {payload['study']['rth_hours']:.2f} h; "
        f"RTH lost to gaps {payload['study']['gap_rth_hours']:.2f} h)."
    )
    a("")
    if ch.get("proceed_note"):
        a(ch["proceed_note"])
        a("")
    a("## Decision rule (bot DESIGN §1.4)")
    a("")
    a(
        "After **all** corrected costs (Bybit taker + live USDT/USDC basis + "
        "rebalance amortization + Fluxion pool fee + bilateral slip + gas + "
        "withdrawal fees when wired):"
    )
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
    a(
        "- **Both legs required.** A dollar figure alone that clears ≥15 while "
        "stable-symbol count is <3 is still **not** a go."
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
    a("### Cost stack (WHI-909)")
    a("")
    cs = payload["cost_stack"]
    a(
        f"- **Bybit taker:** primary **{cs['primary_taker_bps']} bps** "
        f"(Adventure Zone, measured WHI-959 / bot fee-rate pull). Sensitivity "
        f"also reported at {', '.join(str(x) for x in cs['taker_tiers_bps'])} bps."
    )
    a(
        f"- **USDT/USDC basis:** live per-timestamp USDCUSDT 1m mid → "
        f"`usdc_premium_bps = (mid − 1) × 1e4`. Source: `{cs['basis']['source']}` "
        f"(n={cs['basis']['n']}, median {cs['basis']['median_bps']} bps, "
        f"range [{cs['basis']['min_bps']}, {cs['basis']['max_bps']}] bps). "
        "**Sign:** paying USDC (`buy_fluxion_sell_bybit`) is charged the "
        "premium; receiving USDC is credited. Not a hardcoded 7.5."
    )
    a(
        f"- **Rebalance amortization:** primary **{cs['primary_rebalance_bps']} bps** "
        f"on skew-building direction only (`buy_fluxion_sell_bybit`). Model: "
        f"(fixed withdraw+gas + variable conversion bps) / batch notional. "
        f"Sensitivity at {', '.join(str(x) for x in cs['rebalance_tiers_bps'])} bps "
        f"(1 ≈ USDT0-withdraw+Agni; 11–13.5 ≈ Bybit spot conversion path)."
    )
    a(
        "- **Fluxion pool fee 30 bps:** already inside fee-inclusive AMM quotes "
        "(unchanged; not double-counted)."
    )
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
        f"- **Threshold fit:** highest `min_edge_bps` on the extended "
        f"{m['threshold_sweep_bps']} sweep that still retains ≥ 70% of "
        "zero-threshold capturable profit (knee)."
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
    a(
        f"Primary cost stack: **taker {h['taker_bps']} bps** (believed correct) "
        f"+ **live basis** + **rebalance {h['rebalance_bps']} bps** "
        f"on skew-building direction. Clean RTH before this table: "
        f"**{payload['study']['rth_hours_excl_gap']:.2f} h**."
    )
    a("")
    a("| Metric | Value |")
    a("|--------|------:|")
    a(f"| Study calendar days (span/86400s) | {h['calendar_days']:.3f} |")
    a(f"| Portfolio capturable profit (total, $1000 rung) | {h['profit_total_usd']} USDT |")
    a(f"| **Average capturable profit / day** | **{h['profit_per_day_usd']} USDT/day** |")
    a(f"| §1.4 $/day gate (≥{GO_USDT_PER_DAY}) | {h['dollar_gate']} |")
    a(
        f"| Symbols with fitted stable windows (open, $1000) | "
        f"{h['n_stable_symbols']} |"
    )
    a(f"| §1.4 ≥{GO_MIN_SYMBOLS}-symbol gate | {h['symbol_gate']} |")
    a(f"| Go-list | {', '.join(h['go_list']) if h['go_list'] else '—'} |")
    core = ", ".join(h["core_go_list"]) if h["core_go_list"] else "—"
    a(f"| Core go-list (≥1 USDT/day open fit) | {core} |")
    a(f"| Portfolio $/day excluding HOODx | {h['profit_per_day_ex_hoodx']} |")
    a(f"| **Verdict (both legs)** | **{h['verdict']}** |")
    a("")
    a(h["verdict_detail"])
    a("")
    if h.get("concentration_note"):
        a(h["concentration_note"])
        a("")
    a("## Cost-stack sensitivity matrix")
    a("")
    a(
        "Portfolio AMM $1000 single-flight $/day and stable open-session symbol "
        "count under each (taker × rebalance) cell. Live basis in every cell. "
        f"**Bold** = primary ({PRIMARY_TAKER_BPS} / {PRIMARY_REBALANCE_BPS})."
    )
    a("")
    a("| Taker bps | Rebalance bps | $/day | Stable symbols | $/day gate | Symbol gate | Verdict |")
    a("|----------:|--------------:|------:|---------------:|:----------:|:-----------:|---------|")
    for row in payload["sensitivity"]:
        mark_t = "**" if row["is_primary"] else ""
        a(
            f"| {mark_t}{row['taker_bps']}{mark_t} | "
            f"{mark_t}{row['rebalance_bps']}{mark_t} | "
            f"{mark_t}{row['profit_per_day_usd']}{mark_t} | "
            f"{row['n_stable_symbols']} | {row['dollar_gate']} | "
            f"{row['symbol_gate']} | {row['verdict']} |"
        )
    a("")
    a("## Per-symbol survival (corrected primary stack)")
    a("")
    a(
        "Which symbols still clear positive open-session capturable profit "
        f"at the primary stack (taker {PRIMARY_TAKER_BPS}, rebalance "
        f"{PRIMARY_REBALANCE_BPS}, live basis) on the $500 and $1,000 rungs. "
        "Fit bps is the knee at $1,000 for the best direction."
    )
    a("")
    a(
        "| Symbol | Survive $500? | $500 $/day | Survive $1k? | "
        "$1k $/day | Fit bps | Direction |"
    )
    a(
        "|--------|:-------------:|-----------:|:------------:|"
        "-----------:|--------:|-----------|"
    )
    for row in payload["survival"]:
        a(
            f"| {row['pair_id']} | {'yes' if row['survive_500'] else 'no'} | "
            f"{row['pd_500']} | {'yes' if row['survive_1000'] else 'no'} | "
            f"{row['pd_1000']} | {row['fit_bps']} | {row['direction']} |"
        )
    a("")
    a("## Fitted `min_edge_bps` (AMM, for bot config)")
    a("")
    a(
        "Per (symbol × session) at the **$1,000** rung, best direction by "
        "zero-threshold profit/day. Knee fit = highest threshold retaining ≥70% "
        "of that direction's capturable profit (windows filtered from the T=0 "
        "set by ``peak_edge_bps``). Rows marked **ceiling** hit the top of the "
        f"{SWEEP_CEILING_BPS} bps extended sweep still above the capture bar — "
        "treat that as a lower bound, not a tight optimum (or state that the "
        "optimum is unreachable within 200 bps)."
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
        "Selected thresholds 0 / 10 / 20 / 40 / 60 / 100 bps are tabulated "
        "below; the companion JSON keeps selected rows per series (re-run the "
        "script for the full extended sweep in memory)."
    )
    a("")
    a("### AMM $1,000 — RTH open")
    a("")
    a(
        "| Symbol | Dir | T=0 w/d | T=0 $/d | T=20 w/d | T=20 $/d | "
        "T=40 w/d | T=40 $/d | T=60 w/d | T=60 $/d | T=100 w/d | T=100 $/d |"
    )
    a(
        "|--------|-----|--------:|--------:|---------:|---------:|"
        "---------:|---------:|---------:|---------:|----------:|----------:|"
    )
    for row in payload["amm_open_1000_table"]:
        a(
            f"| {row['pair_id']} | {row['direction'][:16]} | "
            f"{row['t0_wd']:.2f} | {row['t0_pd']} | "
            f"{row['t20_wd']:.2f} | {row['t20_pd']} | "
            f"{row['t40_wd']:.2f} | {row['t40_pd']} | "
            f"{row['t60_wd']:.2f} | {row['t60_pd']} | "
            f"{row['t100_wd']:.2f} | {row['t100_pd']} |"
        )
    a("")
    a("### AMM $1,000 — closed session")
    a("")
    a(
        "| Symbol | Dir | T=0 w/d | T=0 $/d | T=20 w/d | T=20 $/d | "
        "T=40 w/d | T=40 $/d | T=60 w/d | T=60 $/d |"
    )
    a(
        "|--------|-----|--------:|--------:|---------:|---------:|"
        "---------:|---------:|---------:|---------:|"
    )
    for row in payload["amm_closed_1000_table"]:
        a(
            f"| {row['pair_id']} | {row['direction'][:16]} | "
            f"{row['t0_wd']:.2f} | {row['t0_pd']} | "
            f"{row['t20_wd']:.2f} | {row['t20_pd']} | "
            f"{row['t40_wd']:.2f} | {row['t40_pd']} | "
            f"{row['t60_wd']:.2f} | {row['t60_pd']} |"
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


def _verdict_for(
    profit_per_day: Decimal, n_stable: int
) -> tuple[str, str, str, str]:
    """Return (verdict, detail, dollar_gate, symbol_gate)."""
    dollar_ok = profit_per_day >= GO_USDT_PER_DAY
    symbol_ok = n_stable >= GO_MIN_SYMBOLS
    dollar_gate = "PASS" if dollar_ok else "FAIL"
    symbol_gate = "PASS" if symbol_ok else "FAIL"
    if dollar_ok and symbol_ok:
        verdict = "GO"
        detail = (
            f"Portfolio average **{profit_per_day:.4f} USDT/day** meets the "
            f"≥{GO_USDT_PER_DAY} gate **and** stable open-session symbols = "
            f"**{n_stable}** (≥{GO_MIN_SYMBOLS}). Both legs of DESIGN §1.4 clear."
        )
    elif profit_per_day < NOGO_USDT_PER_DAY:
        verdict = "NO-GO"
        detail = (
            f"Portfolio average **{profit_per_day:.4f} USDT/day** is below the "
            f"{NOGO_USDT_PER_DAY} USDT/day no-go floor. Stable symbols: "
            f"{n_stable} (need ≥{GO_MIN_SYMBOLS}). Do not size beyond a "
            "minimum proving stage."
        )
    else:
        verdict = "BORDERLINE" if dollar_ok or n_stable > 0 else "NO-GO"
        # Explicit about which leg failed.
        if dollar_ok and not symbol_ok:
            verdict = "NO-GO"
            detail = (
                f"Portfolio average **{profit_per_day:.4f} USDT/day** clears "
                f"the ≥{GO_USDT_PER_DAY} dollar gate, but stable open-session "
                f"symbols = **{n_stable}** (need ≥{GO_MIN_SYMBOLS}). "
                f"**§1.4 requires both legs — this is not a go.**"
            )
        elif not dollar_ok and symbol_ok:
            verdict = "BORDERLINE"
            detail = (
                f"Stable symbols = **{n_stable}** clear the ≥{GO_MIN_SYMBOLS} "
                f"leg, but portfolio average **{profit_per_day:.4f} USDT/day** "
                f"sits between {NOGO_USDT_PER_DAY} and {GO_USDT_PER_DAY}."
            )
        else:
            detail = (
                f"Portfolio average **{profit_per_day:.4f} USDT/day** and "
                f"stable symbols = **{n_stable}** — neither leg is a clean go."
            )
    return verdict, detail, dollar_gate, symbol_gate


def _amm_threshold_row(
    pair_id: str, direction: str, analysis: dict[str, Any]
) -> dict[str, Any]:
    def _cell(thr: str) -> tuple[float, str]:
        s = _pick_sweep(analysis["sweep"], thr)
        if not s:
            return 0.0, "0"
        return float(s["windows_per_day"]), str(s["capturable_profit_per_day"])

    t0_wd, t0_pd = _cell("0")
    t20_wd, t20_pd = _cell("20")
    t40_wd, t40_pd = _cell("40")
    t60_wd, t60_pd = _cell("60")
    t100_wd, t100_pd = _cell("100")
    return {
        "pair_id": pair_id,
        "direction": direction,
        "t0_wd": t0_wd,
        "t0_pd": t0_pd,
        "t20_wd": t20_wd,
        "t20_pd": t20_pd,
        "t40_wd": t40_wd,
        "t40_pd": t40_pd,
        "t60_wd": t60_wd,
        "t60_pd": t60_pd,
        "t100_wd": t100_wd,
        "t100_pd": t100_pd,
    }


def _analyze_scenario(
    series: dict[tuple[str, str, str, str, str], list[EdgeSample]],
    *,
    span_ms: int,
    calendar_days: Decimal,
    reentry: int,
    trade_dur: int,
    max_gap: int,
    rebalance_bps: Decimal,
) -> dict[str, Any]:
    """Run window/fit/portfolio analysis on a series dict (post rebalance)."""
    results: dict[str, Any] = {}
    amm_open_1000_table: list[dict[str, Any]] = []
    amm_closed_1000_table: list[dict[str, Any]] = []
    amm_open_500_summary: list[dict[str, Any]] = []
    rfq_open_table: list[dict[str, Any]] = []
    all_amm_1000: list[OpportunityWindow] = []

    adjusted: dict[tuple[str, str, str, str, str], list[EdgeSample]] = {}
    for key, samples in series.items():
        adjusted[key] = apply_rebalance_amortization_many(
            samples, rebalance_amortized_bps=rebalance_bps
        )

    for key, samples in sorted(adjusted.items()):
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

    for bucket in (best_open, best_closed):
        for row in bucket.values():
            row["flag"] = (
                "ceiling"
                if row["fitted"]
                and Decimal(row["min_edge_bps"]) >= SWEEP_CEILING_BPS
                else "—"
            )

    fits_amm_1000 = sorted(
        list(best_open.values()) + list(best_closed.values()),
        key=lambda r: (r["pair_id"], 0 if r["session"] == "open" else 1),
    )

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
    verdict, detail, dollar_gate, symbol_gate = _verdict_for(profit_per_day, n_stable)

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
            f"{', '.join(core_go_list) if core_go_list else '—'}."
        )

    # Survival table: best open dir at $500 and $1000.
    survival: list[dict[str, Any]] = []
    pair_ids = sorted({k[0] for k in adjusted if k[3] == "amm"})
    for pid in pair_ids:
        best500: dict[str, Any] | None = None
        best1000: dict[str, Any] | None = None
        for key, analysis in results.items():
            p, direction, session, venue, size_s = key.split("|")
            if p != pid or venue != "amm" or session != "open":
                continue
            pd = Decimal(analysis["profit_per_day_at_zero"] or "0")
            row = {
                "direction": direction,
                "pd": analysis["profit_per_day_at_zero"],
                "fit_bps": analysis["fit"]["min_edge_bps"],
                "fitted": analysis["fit"]["fitted"],
                "profit": pd,
            }
            if size_s == "500":
                if best500 is None or pd > best500["profit"]:
                    best500 = row
            elif size_s == "1000":
                if best1000 is None or pd > best1000["profit"]:
                    best1000 = row
        survival.append(
            {
                "pair_id": pid,
                "survive_500": bool(
                    best500 and best500["fitted"] and best500["profit"] > 0
                ),
                "pd_500": best500["pd"] if best500 else "0",
                "survive_1000": bool(
                    best1000 and best1000["fitted"] and best1000["profit"] > 0
                ),
                "pd_1000": best1000["pd"] if best1000 else "0",
                "fit_bps": best1000["fit_bps"] if best1000 else "0",
                "direction": best1000["direction"] if best1000 else "—",
            }
        )

    closed_profit = Decimal(0)
    for key, analysis in results.items():
        _p, _d, session, venue, size_s = key.split("|")
        if venue == "amm" and size_s == "1000" and session == "closed":
            closed_profit += Decimal(analysis["profit_at_zero"] or "0")
    rfq_profit = Decimal(0)
    for key, analysis in results.items():
        _p, _d, session, venue, _sz = key.split("|")
        if venue == "rfq" and session == "open":
            rfq_profit += Decimal(analysis["profit_at_zero"] or "0")

    return {
        "results": results,
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
        "survival": survival,
        "port_profit": port_profit,
        "profit_per_day": profit_per_day,
        "profit_ex_hood_day": profit_ex_hood_day,
        "n_stable": n_stable,
        "go_list": go_list,
        "core_go_list": core_go_list,
        "verdict": verdict,
        "verdict_detail": detail,
        "dollar_gate": dollar_gate,
        "symbol_gate": symbol_gate,
        "concentration_note": concentration_note,
        "closed_profit": closed_profit,
        "rfq_profit": rfq_profit,
    }


def _build_series_for_taker(
    *,
    pairs_amm: Sequence[Any],
    pair_feeds: dict[str, dict[str, Any]],
    metrics_cfg: Any,
    quote_decimals: int,
    gaps: Sequence[GapInterval],
    align_ms: int,
    basis_ts: Sequence[int],
    basis_bps: Sequence[Decimal],
    taker_bps: Decimal,
) -> dict[tuple[str, str, str, str, str], list[EdgeSample]]:
    cfg = metrics_cfg.model_copy(update={"bybit_taker_fee_bps": taker_bps})
    series: dict[tuple[str, str, str, str, str], list[EdgeSample]] = defaultdict(list)
    for pair in pairs_amm:
        feeds = pair_feeds[pair.id]
        for size in NOTIONALS:
            samples = build_amm_samples(
                pair=pair,
                books=feeds["books"],
                pools=feeds["pools"],
                depths=feeds["depths"],
                metrics_cfg=cfg,
                quote_decimals=quote_decimals,
                size_usd=size,
                gaps=gaps,
                align_ms=align_ms,
                max_abs_spread_bps=cfg.max_abs_amm_spread_bps,
                basis_ts_ms=basis_ts,
                basis_bps_series=basis_bps,
            )
            for s in samples:
                series[group_key(s)].append(s)
        rfq_samples = build_rfq_samples(
            pair=pair,
            books=feeds["books"],
            rfq_ticks=feeds["rfq"],
            metrics_cfg=cfg,
            gaps=gaps,
            align_ms=align_ms,
            max_trade_usd=MAX_TRADE_USD,
            basis_ts_ms=basis_ts,
            basis_bps_series=basis_bps,
        )
        for s in rfq_samples:
            series[group_key(s)].append(s)
    return series


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

    reader = JournalReader(db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    gaps = load_gaps(reader)
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
        reader.close()
        conn.close()
        return 2

    since_ms = book_span.min_ms
    until_ms = book_span.max_ms
    span_ms = max(1, until_ms - since_ms)
    calendar_days = Decimal(span_ms) / Decimal(86_400_000)

    rth_h, closed_h, rth_ok, closed_ok = rth_closed_hours(
        since_ms, until_ms, metrics_cfg=metrics_cfg, gaps=gaps
    )
    gap_hours = sum((g.end_ms - g.start_ms) for g in gaps) / 3_600_000
    gap_rth = rth_h - rth_ok

    # --- Collector health since WHI-835 (report before any headline) ---
    gap_tuples = [(g.start_ms, g.end_ms) for g in gaps]
    health_until = max(until_ms, int(time.time() * 1000))
    day_rows = downtime_by_day(
        gap_tuples,
        since_ms=WHI835_LAND_MS,
        until_ms=health_until,
        metrics_cfg=metrics_cfg,
    )
    # Per-day RTH loss inside the study window (not the full post-WHI-835 span).
    study_day_rows = downtime_by_day(
        gap_tuples,
        since_ms=since_ms,
        until_ms=until_ms,
        metrics_cfg=metrics_cfg,
    )
    bad_study_days = [
        d for d in study_day_rows if d.rth_gap_hours > 1.0 and d.rth_hours > 0
    ]
    if rth_ok < 5.0:
        proceed_note = (
            f"> **Coverage stop check:** clean RTH is only **{rth_ok:.2f} h** "
            "(< 5 h). Numbers below are provisional — re-run after more clean "
            "RTH. Proceeding because WHI-909 still needs the corrected stack "
            "on the available journal, but fit quality is low."
        )
    elif bad_study_days:
        proceed_note = (
            f"> **Coverage note:** {len(bad_study_days)} study day(s) lost "
            f">1 h RTH to `collector_down` "
            f"({', '.join(d.day for d in bad_study_days)}). "
            f"Effective clean RTH in the study window is still "
            f"**{rth_ok:.2f} h** (improved vs WHI-866's 5.69 h). Headlines "
            "follow; treat thresholds as provisional if concentration is high."
        )
    else:
        proceed_note = (
            f"> **Coverage OK:** study-window clean RTH **{rth_ok:.2f} h**; "
            "no study day exceeded ~1 h RTH loss to `collector_down`."
        )
    print(
        f"collector health since WHI-835: {len(day_rows)} days, "
        f"study clean RTH={rth_ok:.2f}h",
        flush=True,
    )
    print(proceed_note.replace("> **", "").replace("**", ""), flush=True)

    pair_rows = per_pair_book_span(conn)
    pairs_span = []
    for pid, n, mn, mx in pair_rows:
        p = pairs_all.get(pid)
        has_amm = bool(p and p.fluxion.amm is not None)
        notes = []
        if not has_amm:
            notes.append("CEX-only (no Fluxion pool)")
        if pid == "SPCXx":
            notes.append(
                "frequent pricing_anomaly / thin pool — often excluded by gate"
            )
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

    # Live USDCUSDT basis series (cached for reproducibility).
    basis_cache = Path(args.basis_cache)
    print(f"loading USDCUSDT basis series ({basis_cache.name})…", flush=True)
    basis = load_or_fetch_basis_series(
        start_ms=since_ms - 120_000,
        end_ms=until_ms + 60_000,
        cache_path=basis_cache,
        force_fetch=bool(args.force_basis_fetch),
    )
    basis_summary = basis.summary()
    print(
        f"  basis n={basis.n} median={basis_summary.get('median_bps')} bps "
        f"source={basis.source}",
        flush=True,
    )
    if basis.n == 0:
        print(
            "WARNING: empty USDCUSDT series — falling back to config constant "
            f"{metrics_cfg.usdt_usdc_basis_bps} bps",
            file=sys.stderr,
        )

    # Load journal feeds once per pair.
    pair_feeds: dict[str, dict[str, Any]] = {}
    for pair in pairs_amm:
        print(f"  loading {pair.id}…", flush=True)
        books = load_bucketed_books(
            reader, pair.id, sample_ms=sample_ms, since_ms=since_ms, until_ms=until_ms
        )
        pools = load_pools(
            reader, pair.id, since_ms=since_ms - align_ms, until_ms=until_ms
        )
        depths = load_depths(
            reader, pair.id, since_ms=since_ms - align_ms, until_ms=until_ms
        )
        rfq_ticks = load_rfq(reader, pair.id, since_ms=since_ms, until_ms=until_ms)
        pair_feeds[pair.id] = {
            "books": books,
            "pools": pools,
            "depths": depths,
            "rfq": rfq_ticks,
        }
        print(
            f"    books={len(books)} pools={len(pools)} "
            f"depth={len(depths)} rfq={len(rfq_ticks)}",
            flush=True,
        )

    reader.close()
    conn.close()

    # Build samples per taker tier (live basis baked into each sample).
    series_by_taker: dict[Decimal, dict[tuple[str, str, str, str, str], list[EdgeSample]]] = {}
    for taker in TAKER_TIERS_BPS:
        print(f"scoring samples at taker={taker} bps…", flush=True)
        series_by_taker[taker] = _build_series_for_taker(
            pairs_amm=pairs_amm,
            pair_feeds=pair_feeds,
            metrics_cfg=metrics_cfg,
            quote_decimals=ctx.dex.quote_decimals,
            gaps=gaps,
            align_ms=align_ms,
            basis_ts=basis.ts_ms,
            basis_bps=basis.bps,
            taker_bps=taker,
        )
        n = sum(len(v) for v in series_by_taker[taker].values())
        print(f"  fillable samples total={n}", flush=True)

    # Sensitivity matrix + primary detailed scenario.
    sensitivity: list[dict[str, Any]] = []
    primary: dict[str, Any] | None = None
    for taker in TAKER_TIERS_BPS:
        for reb in REBALANCE_TIERS_BPS:
            print(
                f"analyzing taker={taker} rebalance={reb}…",
                flush=True,
            )
            sc = _analyze_scenario(
                series_by_taker[taker],
                span_ms=span_ms,
                calendar_days=calendar_days,
                reentry=reentry,
                trade_dur=trade_dur,
                max_gap=max_gap,
                rebalance_bps=reb,
            )
            is_primary = taker == PRIMARY_TAKER_BPS and reb == PRIMARY_REBALANCE_BPS
            sensitivity.append(
                {
                    "taker_bps": str(taker),
                    "rebalance_bps": str(reb),
                    "profit_per_day_usd": f"{sc['profit_per_day']:.4f}",
                    "n_stable_symbols": sc["n_stable"],
                    "go_list": sc["go_list"],
                    "dollar_gate": sc["dollar_gate"],
                    "symbol_gate": sc["symbol_gate"],
                    "verdict": sc["verdict"],
                    "is_primary": is_primary,
                }
            )
            if is_primary:
                primary = sc

    assert primary is not None

    closed_note = (
        f"AMM $1000 closed-session capturable profit (sum of per-series "
        f"single-flight, **not** portfolio-deconflicted): "
        f"{primary['closed_profit']:.2f} USDT over the span "
        f"({(primary['closed_profit'] / calendar_days) if calendar_days else 0:.2f}/day). "
        "Closed-session RFQ remains two-sided on liquid pairs "
        "(see `docs/references/m4-closed-session-rfq.md`); session ≠ mechanism."
    )
    rfq_vs_amm = (
        f"RFQ open-session sum of per-series capturable profit at T=0: "
        f"{primary['rfq_profit']:.2f} USDT "
        f"({(primary['rfq_profit'] / calendar_days) if calendar_days else 0:.2f}/day). "
        "Not portfolio-deconflicted against AMM; headline go/no-go ignores RFQ."
    )

    elapsed = time.time() - t0
    try:
        db_disp = db_path.resolve().relative_to(_REPO.resolve())
    except ValueError:
        db_disp = Path("data/monitor-bybit-fluxion.db")
    repro = (
        "uv run python scripts/xstocks_edge_quant.py "
        f"--db {db_disp} --sample-ms {sample_ms}"
    )

    ceiling_hits = [
        r
        for r in primary["fits_amm_1000"]
        if r.get("flag") == "ceiling"
    ]
    next_steps = [
        (
            f"Primary verdict **{primary['verdict']}** under taker "
            f"{PRIMARY_TAKER_BPS} / rebalance {PRIMARY_REBALANCE_BPS} / live basis "
            f"({primary['profit_per_day']:.4f} USDT/day, "
            f"{primary['n_stable']} stable symbols)."
        ),
        (
            "WHI-866 comparison: prior GO at 66 USDT/day / 5 symbols used "
            "10 bps taker + 0 basis + 0 rebalance and only 5.69 h clean RTH."
        ),
        (
            "Wire any surviving go-list thresholds into bot M4 (WHI-876) only "
            "after both §1.4 legs pass on a longer clean span."
        ),
        (
            "RFQ fill-firmness remains a separate gate before any RFQ leg "
            "(see WHI-908 on-chain fill validation)."
        ),
    ]
    if ceiling_hits:
        next_steps.insert(
            1,
            f"{len(ceiling_hits)} fit(s) still pin at the {SWEEP_CEILING_BPS} bps "
            "sweep ceiling — those thresholds are lower bounds only.",
        )
    else:
        next_steps.insert(
            1,
            f"No fit pinned at the {SWEEP_CEILING_BPS} bps sweep ceiling "
            "(extended grid bracketed the knee).",
        )

    results = primary["results"]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "repro_command": repro,
        "elapsed_s": round(elapsed, 1),
        "prior_report": "docs/references/m8-xstocks-edge-quant-v1-whi866.md",
        "method": {
            "market": "bybit-fluxion",
            "engine": "monitor.metrics.pnl_v2.compute_pnl_usd",
            "analysis": "monitor.analysis.edge_quant + cost_stack",
            "notionals_usd": "500, 1000",
            "sample_ms": sample_ms,
            "align_ms": align_ms,
            "reentry_cooldown_ms": reentry,
            "trade_duration_ms": trade_dur,
            "max_gap_ms": max_gap,
            "threshold_sweep_bps": (
                f"0..60 step 2, then ..{int(SWEEP_CEILING_BPS)} step 5"
            ),
            "capture_fraction": "0.70",
            "inventory_usd": str(INVENTORY_USD),
            "max_trade_usd": str(MAX_TRADE_USD),
            "pricing_anomaly_gate": str(metrics_cfg.max_abs_amm_spread_bps),
            "bybit_taker_fee_bps_primary": str(PRIMARY_TAKER_BPS),
            "rebalance_amortized_bps_primary": str(PRIMARY_REBALANCE_BPS),
            "basis": "live USDCUSDT 1m mid → premium bps (as-of join)",
        },
        "cost_stack": {
            "primary_taker_bps": str(PRIMARY_TAKER_BPS),
            "taker_tiers_bps": [str(t) for t in TAKER_TIERS_BPS],
            "taker_believed": str(PRIMARY_TAKER_BPS),
            "taker_reason": (
                "WHI-959 / bot authenticated GET /v5/account/fee-rate — "
                "xStocks Adventure Zone maker=taker=20 bps"
            ),
            "primary_rebalance_bps": str(PRIMARY_REBALANCE_BPS),
            "rebalance_tiers_bps": [str(t) for t in REBALANCE_TIERS_BPS],
            "basis": basis_summary,
        },
        "collector_health": {
            "since": _ms_iso(WHI835_LAND_MS),
            "until": _ms_iso(health_until),
            "n_gap_intervals": sum(
                1
                for g in gaps
                if g.end_ms >= WHI835_LAND_MS
            ),
            "total_gap_hours": sum(
                max(0, min(g.end_ms, health_until) - max(g.start_ms, WHI835_LAND_MS))
                for g in gaps
            )
            / 3_600_000,
            "by_day": [
                {
                    "day": d.day,
                    "total_gap_hours": round(d.total_gap_hours, 2),
                    "rth_hours": round(d.rth_hours, 2),
                    "rth_gap_hours": round(d.rth_gap_hours, 2),
                    "rth_clean_hours": round(d.rth_clean_hours, 2),
                }
                for d in day_rows
            ],
            "proceed_note": proceed_note,
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
            {
                "table": "USDCUSDT klines (external)",
                "n_rows": basis.n,
                "min": _ms_iso(basis.ts_ms[0]) if basis.n else "n/a",
                "max": _ms_iso(basis.ts_ms[-1]) if basis.n else "n/a",
                "role": "Live basis series (Bybit public 1m; as-of join)",
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
            f"pricing_anomaly, default |spread| > "
            f"{metrics_cfg.max_abs_amm_spread_bps} bps) are excluded from "
            "AMM samples for that timestamp (SPCXx frequently hits this).",
            "CEX-only inventory pairs (no Fluxion AMM) are out of scope for "
            "the bot v1 AMM path and appear only in the coverage table.",
            "Headline portfolio mixes open+closed AMM $1000 windows at T=0 "
            "(one trade per window, single-flight). Go-list symbols are "
            "open-session fits only.",
            "Basis is live per-timestamp USDCUSDT premium (not the panel "
            "constant 7.5); rebalance amortization charged only on "
            "buy_fluxion_sell_bybit.",
        ],
        "headline": {
            "taker_bps": str(PRIMARY_TAKER_BPS),
            "rebalance_bps": str(PRIMARY_REBALANCE_BPS),
            "calendar_days": float(calendar_days),
            "profit_total_usd": f"{primary['port_profit']:.4f}",
            "profit_per_day_usd": f"{primary['profit_per_day']:.4f}",
            "profit_per_day_ex_hoodx": f"{primary['profit_ex_hood_day']:.4f}",
            "n_stable_symbols": primary["n_stable"],
            "go_list": primary["go_list"],
            "core_go_list": primary["core_go_list"],
            "dollar_gate": primary["dollar_gate"],
            "symbol_gate": primary["symbol_gate"],
            "verdict": primary["verdict"],
            "verdict_detail": primary["verdict_detail"],
            "concentration_note": primary["concentration_note"],
        },
        "sensitivity": sensitivity,
        "survival": primary["survival"],
        "fits_amm_1000": primary["fits_amm_1000"],
        "amm_open_1000_table": primary["amm_open_1000_table"],
        "amm_closed_1000_table": primary["amm_closed_1000_table"],
        "amm_open_500_summary": primary["amm_open_500_summary"],
        "rfq_open_table": primary["rfq_open_table"],
        "closed_note": closed_note,
        "rfq_vs_amm_note": rfq_vs_amm,
        "next_steps": next_steps,
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
                    if row["min_edge_bps"]
                    in ("0", "10", "20", "40", "60", "100", "150", "200")
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
    json_path.write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(f"\nWrote {report_path}")
    print(f"Wrote {json_path}")
    print(
        f"Verdict: {primary['verdict']}  "
        f"({primary['profit_per_day']:.2f} USDT/day, "
        f"{primary['n_stable']} symbols)  "
        f"gates $/day={primary['dollar_gate']} symbols={primary['symbol_gate']}"
    )
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
    p.add_argument(
        "--basis-cache",
        default=str(DEFAULT_BASIS_CACHE),
        help="Cache path for USDCUSDT 1m klines (reproducibility)",
    )
    p.add_argument(
        "--force-basis-fetch",
        action="store_true",
        help="Ignore basis cache and re-fetch from Bybit",
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
