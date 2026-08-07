#!/usr/bin/env python3
"""WHI-915 / M8: delay-decay — does the edge survive a sequential transfer cycle?

Reconstructs the bot's zero-inventory cycle (Fluxion buy at t, Bybit sell at
t+N) on the bybit-fluxion journal. Direction 1 only (buy_fluxion_sell_bybit).

Writes:
  docs/references/m8-delay-decay.md
  docs/references/m8-delay-decay.json

Usage (repo root; journal must exist):

  uv run python scripts/xstocks_delay_decay.py
  uv run python scripts/xstocks_delay_decay.py --db data/monitor-bybit-fluxion.db
  uv run python scripts/xstocks_delay_decay.py --sample-ms 20000
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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from monitor.analysis.delay_decay import (  # noqa: E402
    DEFAULT_WIN_RATE_TARGETS,
    DriftPremiumFit,
    SequentialOutcome,
    clip_size_sweep,
    distribution_stats,
    fit_drift_premium_k,
    optimal_clip_size,
    portfolio_sequential_profit,
    sequential_capturable_profit,
    transit_sigma_bps,
    withdraw_fee_bps,
)
from monitor.analysis.edge_quant import (  # noqa: E402
    EdgeSample,
    OpportunityWindow,
    capturable_profit_single_flight,
    detect_windows,
    portfolio_capturable_profit,
)
from monitor.markets import load_market_context  # noqa: E402
from monitor.metrics.amm_pool import amm_pool_from_pair_tick  # noqa: E402
from monitor.metrics.amm_quote import amm_quote_for_cex  # noqa: E402
from monitor.metrics.edge import mid_from_bid_ask  # noqa: E402
from monitor.metrics.pnl_snapshot import levels_from_depth_curve  # noqa: E402
from monitor.metrics.pnl_v2 import (  # noqa: E402
    bybit_sell_proceeds_usd,
    compute_pnl_usd,
)
from monitor.metrics.session import session_kind  # noqa: E402
from monitor.quotes import (  # noqa: E402
    BybitBookTick,
    BybitDepthTick,
    FluxionPoolStateTick,
)
from monitor.storage.reader import (  # noqa: E402
    _row_to_bybit_book,
    _row_to_bybit_depth,
    _row_to_pool_state,
)
from monitor.symbols.models import Pair  # noqa: E402

# Bot DESIGN §1.4 gates (same as M8 simultaneous report).
GO_USDT_PER_DAY = Decimal("15")
NOGO_USDT_PER_DAY = Decimal("5")
GO_MIN_SYMBOLS = 3
MAX_TRADE_USD = Decimal("1000")
INVENTORY_USD = Decimal("5000")

# Sequential study defaults.
CLIP_SIZES = (
    Decimal("100"),
    Decimal("250"),
    Decimal("500"),
    Decimal("1000"),
)
# Transit horizons in minutes; 60 is the deposit-timeout sensitivity case.
LAG_MINUTES = (5, 10, 15, 20, 30, 60)
PRIMARY_LAG_MIN = 10
PRIMARY_SIZE = Decimal("500")
DEFAULT_WITHDRAW_FEE_USD = Decimal("1")  # flat recycle fee under §3.1 A+D
DEFAULT_REENTRY_COOLDOWN_MS = 86_400_000  # one entry / window / day
DEFAULT_TRADE_DURATION_MS = 5_000
DEFAULT_MAX_GAP_MS = 120_000
DEFAULT_SAMPLE_MS = 20_000
DEFAULT_ALIGN_MS = 15_000
DEFAULT_REPORT = _REPO / "docs" / "references" / "m8-delay-decay.md"
DEFAULT_JSON = _REPO / "docs" / "references" / "m8-delay-decay.json"
DIRECTION = "buy_fluxion_sell_bybit"
SCHEMA_VERSION = 1


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


def _fmt_dec(v: Decimal | str | int | float | None, places: int = 4) -> str:
    if v is None:
        return "n/a"
    d = Decimal(str(v))
    if d == 0:
        return "0"
    q = Decimal(10) ** -places
    return format(d.quantize(q), "f")


def _fmt_pct(v: Decimal | None) -> str:
    if v is None:
        return "n/a"
    return f"{(v * Decimal(100)).quantize(Decimal('0.1'))}%"


def _in_gap(ts_ms: int, gaps: Sequence[GapInterval]) -> bool:
    for g in gaps:
        if g.start_ms <= ts_ms <= g.end_ms:
            return True
        if g.start_ms > ts_ms:
            break
    return False


def _as_of_idx(ts_list: Sequence[int], ts_ms: int) -> int | None:
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
        GapInterval(start_ms=int(r[1]), end_ms=int(r[2]), source=str(r[0]))
        for r in rows
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


def load_bucketed_books(
    conn: sqlite3.Connection,
    pair_id: str,
    *,
    sample_ms: int,
    since_ms: int,
    until_ms: int,
) -> list[BybitBookTick]:
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


def rth_closed_hours(
    start_ms: int,
    end_ms: int,
    *,
    metrics_cfg: Any,
    gaps: Sequence[GapInterval],
) -> tuple[float, float, float, float]:
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


@dataclass(frozen=True, slots=True)
class EntryQuote:
    """Entry-time legs for one clip (direction 1)."""

    ts_ms: int
    session: str
    size_usd: Decimal
    q_base: Decimal
    spent_usd: Decimal
    sim_recv_usd: Decimal
    sim_pnl_usd: Decimal
    sim_edge_bps: Decimal
    fluxion_impact_bps: Decimal
    gas_usd: Decimal
    basis_usd: Decimal


def build_entries(
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
    # Only keep samples with simultaneous edge > 0 (opportunity windows).
    require_positive_sim: bool = True,
) -> list[EntryQuote]:
    if not books or not pools:
        return []
    pool_ts = [p.recv_ts_ms for p in pools]
    depth_ts = [d.recv_ts_ms for d in depths]
    out: list[EntryQuote] = []
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
        bids = None
        di = _as_of_idx(depth_ts, ts) if depth_ts else None
        if di is not None and ts - depths[di].recv_ts_ms <= align_ms:
            bids = levels_from_depth_curve(depths[di], side="bid") or None
        r = compute_pnl_usd(
            pair_id=pair.id,
            bybit_bid=book.bid_de_multiplied,
            bybit_ask=book.ask_de_multiplied,
            size_usd=size_usd,
            direction=DIRECTION,  # type: ignore[arg-type]
            venue="amm",
            config=metrics_cfg,
            amm=amm,
            bybit_bids=bids,
        )
        if not r.fillable or r.pnl_bps is None:
            continue
        if require_positive_sim and r.pnl_usd <= 0:
            continue
        impact_bps = (
            r.costs.fluxion_slip_usd / size_usd * Decimal(10_000)
            if size_usd > 0
            else Decimal(0)
        )
        sess = session_kind(
            datetime.fromtimestamp(ts / 1000, tz=UTC), config=metrics_cfg
        ).value
        out.append(
            EntryQuote(
                ts_ms=ts,
                session=sess,
                size_usd=size_usd,
                q_base=r.q_base,
                spent_usd=r.spent_usd,
                sim_recv_usd=r.recv_usd,
                sim_pnl_usd=r.pnl_usd,
                sim_edge_bps=r.pnl_bps,
                fluxion_impact_bps=impact_bps,
                gas_usd=r.costs.gas_usd,
                basis_usd=r.costs.basis_usd,
            )
        )
    return out


def bybit_recv_at(
    *,
    books: Sequence[BybitBookTick],
    book_ts: Sequence[int],
    depths: Sequence[BybitDepthTick],
    depth_ts: Sequence[int],
    target_ms: int,
    q_base: Decimal,
    metrics_cfg: Any,
    align_ms: int,
) -> Decimal | None:
    """Bybit sell proceeds for ``q_base`` as-of ``target_ms`` (fee-inclusive)."""
    bi = _as_of_idx(book_ts, target_ms)
    if bi is None:
        return None
    book = books[bi]
    if target_ms - book.recv_ts_ms > align_ms:
        return None
    bids = None
    di = _as_of_idx(depth_ts, target_ms) if depth_ts else None
    if di is not None and target_ms - depths[di].recv_ts_ms <= align_ms:
        bids = levels_from_depth_curve(depths[di], side="bid") or None
    return bybit_sell_proceeds_usd(
        bybit_bid=book.bid_de_multiplied,
        bybit_ask=book.ask_de_multiplied,
        q_base=q_base,
        config=metrics_cfg,
        bybit_bids=bids,
    )


def realised_from_entry(
    entry: EntryQuote,
    recv_later: Decimal,
    *,
    withdraw_fee_usd: Decimal,
) -> tuple[Decimal, Decimal]:
    """Realised PnL USD and bps after delayed Bybit sell + flat withdraw fee."""
    # sim_pnl = sim_recv - spent - gas - basis
    # realised = recv_later - spent - gas - basis - withdraw
    pnl = (
        entry.sim_pnl_usd
        + (recv_later - entry.sim_recv_usd)
        - withdraw_fee_usd
    )
    bps = pnl / entry.size_usd * Decimal(10_000) if entry.size_usd > 0 else Decimal(0)
    return pnl, bps


def collect_mids(
    books: Sequence[BybitBookTick],
    *,
    gaps: Sequence[GapInterval],
    metrics_cfg: Any,
) -> dict[str, list[tuple[int, Decimal]]]:
    """Session-split (ts, mid) series for transit σ."""
    out: dict[str, list[tuple[int, Decimal]]] = {"open": [], "closed": [], "all": []}
    for book in books:
        ts = book.recv_ts_ms
        if _in_gap(ts, gaps):
            continue
        mid = mid_from_bid_ask(book.bid_de_multiplied, book.ask_de_multiplied)
        if mid is None or mid <= 0:
            continue
        sess = session_kind(
            datetime.fromtimestamp(ts / 1000, tz=UTC), config=metrics_cfg
        ).value
        out[sess].append((ts, mid))
        out["all"].append((ts, mid))
    return out


def fire_on_open_entries(
    entries: Sequence[EntryQuote],
    *,
    max_gap_ms: int,
) -> list[EntryQuote]:
    """Collapse dense positive-edge samples into opportunity-window opens.

    Uses edge_quant window detection on synthetic EdgeSamples, then maps each
    window start back to the entry quote (fire-on-open).
    """
    if not entries:
        return []
    # Group by session so open/closed don't glue.
    by_sess: dict[str, list[EntryQuote]] = defaultdict(list)
    for e in entries:
        by_sess[e.session].append(e)
    selected: list[EntryQuote] = []
    for sess, group in by_sess.items():
        group = sorted(group, key=lambda e: e.ts_ms)
        samples = [
            EdgeSample(
                ts_ms=e.ts_ms,
                pair_id="x",
                direction=DIRECTION,
                session=sess,  # type: ignore[arg-type]
                venue="amm",
                size_usd=e.size_usd,
                edge_bps=e.sim_edge_bps,
                pnl_usd=e.sim_pnl_usd,
            )
            for e in group
        ]
        wins = detect_windows(
            samples, min_edge_bps=Decimal(0), max_gap_ms=max_gap_ms
        )
        by_ts = {e.ts_ms: e for e in group}
        for w in wins:
            e = by_ts.get(w.start_ms)
            if e is not None:
                selected.append(e)
    return sorted(selected, key=lambda e: e.ts_ms)


def outcomes_for_lag(
    entries: Sequence[EntryQuote],
    *,
    books: Sequence[BybitBookTick],
    depths: Sequence[BybitDepthTick],
    metrics_cfg: Any,
    lag_ms: int,
    align_ms: int,
    withdraw_fee_usd: Decimal,
    pair_id: str,
) -> list[SequentialOutcome]:
    book_ts = [b.recv_ts_ms for b in books]
    depth_ts = [d.recv_ts_ms for d in depths]
    out: list[SequentialOutcome] = []
    for e in entries:
        recv = bybit_recv_at(
            books=books,
            book_ts=book_ts,
            depths=depths,
            depth_ts=depth_ts,
            target_ms=e.ts_ms + lag_ms,
            q_base=e.q_base,
            metrics_cfg=metrics_cfg,
            align_ms=align_ms,
        )
        if recv is None:
            continue
        pnl, bps = realised_from_entry(e, recv, withdraw_fee_usd=withdraw_fee_usd)
        out.append(
            SequentialOutcome(
                pair_id=pair_id,
                session=e.session,  # type: ignore[arg-type]
                size_usd=e.size_usd,
                lag_ms=lag_ms,
                entry_ts_ms=e.ts_ms,
                simultaneous_edge_bps=e.sim_edge_bps,
                simultaneous_pnl_usd=e.sim_pnl_usd,
                realised_pnl_usd=pnl,
                realised_bps=bps,
                fillable=True,
            )
        )
    return out


def build_report(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    a = lines.append
    h = payload["headline"]
    m = payload["method"]

    a("# Delay-decay: sequential transfer cycle edge (WHI-915 / M8)")
    a("")
    a(
        "Does the bot's **zero-inventory transfer cycle** still earn money when "
        "the legs are sequential? Fluxion buy at opportunity-window open, Bybit "
        "sell at `t + N`, full cost stack including the flat recycle withdraw fee. "
        "Companion to the simultaneous M0 report "
        "(`docs/references/m8-xstocks-edge-quant.md`)."
    )
    a("")
    a(f"**Generated:** {payload['generated_utc']}")
    a("")
    a("## Regeneration")
    a("")
    a("```bash")
    a(payload["regen_cmd"])
    a("```")
    a("")
    a("Pure helpers: `monitor.analysis.delay_decay` (unit-tested).")
    a("Venue math: `monitor.metrics.pnl_v2.compute_pnl_usd` + delayed Bybit sell.")
    a("")
    a("## Method")
    a("")
    a("| Item | Value |")
    a("|------|--------|")
    for k, v in m.items():
        a(f"| {k} | {v} |")
    a("")
    a("### Cycle reconstruction")
    a("")
    a(
        "1. Detect opportunity windows on **simultaneous** net edge "
        f"(`{DIRECTION}` only) at each clip size; fire on window open."
    )
    a(
        "2. At entry: executable Fluxion buy (fee + impact) and same-timestamp "
        "Bybit sell (baseline simultaneous PnL)."
    )
    a(
        "3. At `t + N`: Bybit sell VWAP for the **same base qty**, fee-inclusive."
    )
    a(
        "4. Realised PnL = delayed Bybit proceeds − Fluxion spend − gas − "
        "USDT/USDC basis − flat withdraw fee."
    )
    a(
        "5. Transit σ: sample std of Bybit mid log-returns over each lag "
        "(session-split)."
    )
    a(
        "6. `drift_premium_k`: minimal k on a 0.1 grid such that "
        "`simultaneous_edge ≥ k · σ` yields the target realised win rate "
        "(min 5 admitted samples)."
    )
    a("")
    a("## Data span")
    a("")
    a(payload["data_span_note"])
    a("")
    a("| Table | Rows | Min (UTC) | Max (UTC) |")
    a("|-------|-----:|-----------|-----------|")
    for row in payload["spans"]:
        a(
            f"| `{row['table']}` | {row['n_rows']:,} | "
            f"{row['min_utc']} | {row['max_utc']} |"
        )
    a("")
    a("## Headline (sequential vs simultaneous)")
    a("")
    a("| Metric | Value |")
    a("|--------|------:|")
    a(f"| Study calendar days | {h['calendar_days']} |")
    a(f"| Primary lag | {h['primary_lag_min']} min |")
    a(f"| Primary clip | ${h['primary_size_usd']} |")
    a(f"| Withdraw fee (flat) | ${h['withdraw_fee_usd']} / cycle |")
    a(
        f"| Simultaneous portfolio $/day (baseline, no withdraw fee) | "
        f"{h['sim_portfolio_per_day']} |"
    )
    a(
        f"| Sequential portfolio $/day (realised, w/ withdraw fee) | "
        f"{h['seq_portfolio_per_day']} |"
    )
    a(f"| Sequential / simultaneous ratio | {h['seq_over_sim_ratio']} |")
    a(f"| Sequential go-list (open, primary) | {h['go_list'] or '—'} |")
    a(f"| Symbols clearing ≥1 USDT/day sequential | {h['core_go_list'] or '—'} |")
    a(f"| **Verdict** | **{h['verdict']}** |")
    a("")
    a(h["verdict_detail"])
    a("")
    a(
        "> **Headline caveat:** the sequential/simultaneous ratio is **not** "
        "an M0 apples-to-apples edge comparison. Simultaneous here is "
        "direction-1 fire-on-open paper PnL on the **same sparse windows** "
        "(n open windows is small on this span); sequential adds favourable "
        "or adverse transit drift plus the flat withdraw fee. A ratio > 1 "
        "usually means the delayed sell luckily improved a few cycles — "
        "not that delay is free. See universe note below."
    )
    a("")

    a("## Measured transit-window σ (Bybit mid log-return, bps)")
    a("")
    a(
        "Replaces the ~40 bps / 10 min HOODx estimate. 1σ sample std; "
        "RTH and closed reported separately."
    )
    a("")
    a("| Symbol | Session | N=5m | N=10m | N=15m | N=20m | N=30m | N=60m | n@10m |")
    a("|--------|---------|-----:|------:|------:|------:|------:|------:|------:|")
    for row in payload["transit_sigma"]:
        a(
            f"| {row['pair_id']} | {row['session']} | "
            f"{row['s5']} | {row['s10']} | {row['s15']} | "
            f"{row['s20']} | {row['s30']} | {row['s60']} | {row['n10']} |"
        )
    a("")

    a("## Realised PnL distribution (primary clip, fire-on-open windows)")
    a("")
    a(
        f"Clip **${m['primary_size_usd']}**, all symbols, per session × lag. "
        "Bps are net of the full cost stack including the flat withdraw fee. "
        "Win = realised USD > 0."
    )
    a("")
    a("### RTH open")
    a("")
    a(
        "| Symbol | N (min) | n | Win% | Mean bps | Med bps | p5 | p25 | p75 | p95 | "
        "Worst | Mean USD |"
    )
    a(
        "|--------|--------:|--:|-----:|---------:|--------:|---:|----:|----:|----:|"
        "------:|---------:|"
    )
    for row in payload["dist_open"]:
        a(
            f"| {row['pair_id']} | {row['lag_min']} | {row['n']} | "
            f"{row['win_rate']} | {row['mean_bps']} | {row['median_bps']} | "
            f"{row['p5_bps']} | {row['p25_bps']} | {row['p75_bps']} | "
            f"{row['p95_bps']} | {row['worst_bps']} | {row['mean_usd']} |"
        )
    a("")
    a(
        "Nearest-rank percentiles: at n < 11, p5 often equals worst (small-sample "
        "artefact — not a distinct tail estimate)."
    )
    a("")
    a("### Closed session")
    a("")
    a(
        "| Symbol | N (min) | n | Win% | Mean bps | Med bps | p5 | p25 | p75 | p95 | "
        "Worst | Mean USD |"
    )
    a(
        "|--------|--------:|--:|-----:|---------:|--------:|---:|----:|----:|----:|"
        "------:|---------:|"
    )
    for row in payload["dist_closed"]:
        a(
            f"| {row['pair_id']} | {row['lag_min']} | {row['n']} | "
            f"{row['win_rate']} | {row['mean_bps']} | {row['median_bps']} | "
            f"{row['p5_bps']} | {row['p25_bps']} | {row['p75_bps']} | "
            f"{row['p95_bps']} | {row['worst_bps']} | {row['mean_usd']} |"
        )
    a("")

    a("## `drift_premium_k` (engine deliverable)")
    a("")
    a(
        f"At primary lag **{h['primary_lag_min']} min** and clip **$"
        f"{h['primary_size_usd']}**, using session-matched σ. "
        "Admission: `edge_bps ≥ k · σ_transit`. Report k at 90 / 95 / 99% "
        "target realised win rates. Cells show **n/a** when the target is "
        "unreachable (too few windows or no k on the 0–5 grid clears it); "
        "min_admitted is 5 when n≥5 else 3 (provisional on thin spans)."
    )
    a("")
    a(
        "| Symbol | Session | σ@primary (bps) | k@90% | wr | n | k@95% | wr | n | "
        "k@99% | wr | n | Reachable? |"
    )
    a(
        "|--------|---------|----------------:|------:|---:|--:|------:|---:|--:|"
        "------:|---:|--:|-----------|"
    )
    for row in payload["drift_premium_k"]:
        a(
            f"| {row['pair_id']} | {row['session']} | {row['sigma_bps']} | "
            f"{row['k90']} | {row['wr90']} | {row['n90']} | "
            f"{row['k95']} | {row['wr95']} | {row['n95']} | "
            f"{row['k99']} | {row['wr99']} | {row['n99']} | "
            f"{row['reachable']} |"
        )
    a("")
    a(payload["drift_premium_note"])
    a("")

    a("## Clip-size sweep (fee vs Fluxion impact)")
    a("")
    a(
        f"Flat withdraw fee = **${h['withdraw_fee_usd']}** "
        f"({withdraw_fee_bps(Decimal(500), Decimal(h['withdraw_fee_usd']))} bps "
        f"on $500, "
        f"{withdraw_fee_bps(Decimal(1000), Decimal(h['withdraw_fee_usd']))} bps "
        f"on $1,000). Primary lag {h['primary_lag_min']} min, RTH open fire-on-open. "
        "Optimum = highest mean realised USD / cycle."
    )
    a("")
    a(
        "| Symbol | Size | n | Mean bps | Med bps | Mean USD | Win% | "
        "Fee bps | Mean Flux impact bps | Optimum? |"
    )
    a(
        "|--------|-----:|--:|---------:|--------:|---------:|-----:|"
        "-------:|---------------------:|---------|"
    )
    for row in payload["clip_sweep"]:
        a(
            f"| {row['pair_id']} | {row['size_usd']} | {row['n']} | "
            f"{row['mean_bps']} | {row['median_bps']} | {row['mean_usd']} | "
            f"{row['win_rate']} | {row['fee_bps']} | {row['impact_bps']} | "
            f"{row['optimum']} |"
        )
    a("")
    a("### Stated optimum per symbol (RTH open, primary lag)")
    a("")
    a(
        "| Symbol | Optimum size | n | Mean USD / cycle | Mean bps | Win% | Note |"
    )
    a(
        "|--------|-------------:|--:|-----------------:|---------:|-----:|------|"
    )
    for row in payload["clip_optimum"]:
        a(
            f"| {row['pair_id']} | ${row['size_usd']} | {row.get('n', 'n/a')} | "
            f"{row['mean_usd']} | {row['mean_bps']} | {row['win_rate']} | "
            f"{row.get('note', '')} |"
        )
    a("")
    a("### Closed-session optimum (diagnostic only)")
    a("")
    a(
        "Not used for live RTH sizing. Surfaces any ≥n sizes when open-session "
        "windows are too sparse."
    )
    a("")
    a(
        "| Symbol | Optimum size | n | Mean USD / cycle | Mean bps | Win% | Note |"
    )
    a(
        "|--------|-------------:|--:|-----------------:|---------:|-----:|------|"
    )
    for row in payload.get("clip_optimum_closed", []):
        a(
            f"| {row['pair_id']} | ${row['size_usd']} | {row.get('n', 'n/a')} | "
            f"{row['mean_usd']} | {row['mean_bps']} | {row['win_rate']} | "
            f"{row.get('note', '')} |"
        )
    a("")

    a("## Symbol universe under sequential model")
    a("")
    a(
        "Per-symbol series-level capturable profit/day at primary lag + primary "
        "clip, single-flight with 1-day re-entry cooldown (one trade per window "
        "day). Sequential books **realised** PnL; simultaneous books paper "
        "simultaneous PnL **without** the flat withdraw fee (M0 parity)."
    )
    a("")
    a(
        "| Symbol | Session | Sim $/day | Seq $/day | Seq win% | "
        "Windows | Seq ≥1 $/day? |"
    )
    a(
        "|--------|---------|----------:|----------:|---------:|"
        "--------:|--------------|"
    )
    for row in payload["universe"]:
        a(
            f"| {row['pair_id']} | {row['session']} | {row['sim_per_day']} | "
            f"{row['seq_per_day']} | {row['win_rate']} | {row['n_windows']} | "
            f"{row['clears']} |"
        )
    a("")
    a(payload["universe_note"])
    a("")

    a("## Sensitivity: N = 30 and 60 minutes")
    a("")
    a(
        "Deposit-timeout evidence base. Same primary clip, RTH open portfolio "
        "single-flight realised $/day and mean realised bps."
    )
    a("")
    a(
        "| Lag (min) | $/day (all) | Win% (all) | Mean bps | n | "
        "$/day (paired w/ primary) | Win% (paired) | n_paired |"
    )
    a(
        "|----------:|-----------:|-----------:|---------:|--:|"
        "-------------------------:|--------------:|---------:|"
    )
    for row in payload["lag_sensitivity"]:
        a(
            f"| {row['lag_min']} | {row['portfolio_per_day']} | "
            f"{row['win_rate']} | {row['mean_bps']} | {row['n']} | "
            f"{row.get('paired_portfolio_per_day', 'n/a')} | "
            f"{row.get('paired_win_rate', 'n/a')} | "
            f"{row.get('paired_n', 'n/a')} |"
        )
    a("")
    a(payload["lag_sensitivity_note"])
    a("")

    a("## Fit quality & caveats")
    a("")
    for line in payload["caveats"]:
        a(f"- {line}")
    a("")
    a("## What the bot should consume")
    a("")
    for line in payload["bot_consume"]:
        a(f"- {line}")
    a("")
    a("---")
    a("")
    a(
        f"*Companion machine-readable payload: "
        f"`docs/references/m8-delay-decay.json` "
        f"(schema version {payload['schema_version']}).*"
    )
    a("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    t0 = time.time()
    db_path = Path(args.db).resolve()
    if not db_path.is_file():
        print(f"journal not found: {db_path}", file=sys.stderr)
        return 2

    withdraw_fee = Decimal(str(args.withdraw_fee_usd))
    sample_ms = int(args.sample_ms)
    align_ms = int(args.align_ms)
    max_gap_ms = int(args.max_gap_ms)
    reentry_ms = int(args.reentry_cooldown_ms)
    trade_ms = int(args.trade_duration_ms)
    primary_lag_ms = PRIMARY_LAG_MIN * 60_000
    lag_ms_list = [m * 60_000 for m in LAG_MINUTES]

    ctx = load_market_context("bybit-fluxion", load_collector=False, repo_root=_REPO)
    assert ctx.pairs is not None
    metrics_cfg = ctx.metrics
    pairs_amm = ctx.pairs.pairs_with_amm()
    quote_decimals = ctx.dex.quote_decimals
    max_abs = metrics_cfg.max_abs_amm_spread_bps

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    gaps = load_gaps(conn)
    spans_raw = {
        "bybit_book": table_span(conn, "bybit_book", "recv_ts_ms"),
        "bybit_depth": table_span(conn, "bybit_depth", "recv_ts_ms"),
        "fluxion_pool_state": table_span(conn, "fluxion_pool_state", "recv_ts_ms"),
        "collector_gaps": table_span(conn, "collector_gaps", "gap_start_ms"),
    }
    book_span = spans_raw["bybit_book"]
    if book_span.min_ms is None or book_span.max_ms is None:
        print("bybit_book is empty — cannot run study", file=sys.stderr)
        return 2

    since_ms = book_span.min_ms
    until_ms = book_span.max_ms
    # Need lag headroom at the end of the series for delayed sells.
    max_lag = max(lag_ms_list)
    span_ms = max(1, until_ms - since_ms)
    calendar_days = Decimal(span_ms) / Decimal(86_400_000)
    # Effective days for profit/day: exclude the terminal lag where sells cannot
    # complete. Conservative: use full calendar_days (understates slightly if
    # late windows are dropped).
    days = calendar_days if calendar_days > 0 else Decimal(1)

    rth_h, closed_h, rth_ok, closed_ok = rth_closed_hours(
        since_ms, until_ms, metrics_cfg=metrics_cfg, gaps=gaps
    )
    gap_hours = sum((g.end_ms - g.start_ms) for g in gaps) / 3_600_000

    # Per-pair work.
    # outcomes[pair][size][lag_ms][session] -> list[SequentialOutcome]
    all_outcomes: dict[
        str, dict[Decimal, dict[int, dict[str, list[SequentialOutcome]]]]
    ] = {}
    # fire-on-open entries for impact stats
    impact_by_pair_size: dict[tuple[str, Decimal], list[Decimal]] = defaultdict(list)
    transit_rows: list[dict[str, Any]] = []
    sigma_lookup: dict[tuple[str, str, int], Decimal] = {}

    for pair in pairs_amm:
        print(f"… {pair.id}", flush=True)
        # Extend book load by max_lag so delayed sells can resolve.
        books = load_bucketed_books(
            conn,
            pair.id,
            sample_ms=sample_ms,
            since_ms=since_ms,
            until_ms=until_ms + max_lag,
        )
        pools = load_pools(
            conn,
            pair.id,
            since_ms=since_ms - align_ms,
            until_ms=until_ms + max_lag,
        )
        depths = load_depths(
            conn,
            pair.id,
            since_ms=since_ms - align_ms,
            until_ms=until_ms + max_lag,
        )
        if not books or not pools:
            continue

        mids = collect_mids(books, gaps=gaps, metrics_cfg=metrics_cfg)
        # σ as-of: allow one sample bucket of slack so sample_ms > align_ms
        # does not systematically drop returns.
        sigma_align_ms = max(align_ms, sample_ms)
        for sess in ("open", "closed", "all"):
            row: dict[str, Any] = {
                "pair_id": pair.id,
                "session": sess,
                "n10": 0,
                "n_by_lag": {},
            }
            for mins in LAG_MINUTES:
                sig, n = transit_sigma_bps(
                    mids[sess],
                    lag_ms=mins * 60_000,
                    max_align_ms=sigma_align_ms,
                )
                sk = f"s{mins}"
                row[sk] = _fmt_dec(sig, 2) if sig is not None else "n/a"
                row["n_by_lag"][str(mins)] = n
                if mins == 10:
                    row["n10"] = n
                if sig is not None:
                    sigma_lookup[(pair.id, sess, mins * 60_000)] = sig
            transit_rows.append(row)

        all_outcomes[pair.id] = {}
        for size in CLIP_SIZES:
            entries_dense = build_entries(
                pair=pair,
                books=[b for b in books if b.recv_ts_ms <= until_ms],
                pools=pools,
                depths=depths,
                metrics_cfg=metrics_cfg,
                quote_decimals=quote_decimals,
                size_usd=size,
                gaps=gaps,
                align_ms=align_ms,
                max_abs_spread_bps=max_abs,
                require_positive_sim=True,
            )
            entries = fire_on_open_entries(entries_dense, max_gap_ms=max_gap_ms)
            for e in entries:
                # Clip sweep is RTH-open only — keep impact diagnostic matched.
                if e.session == "open":
                    impact_by_pair_size[(pair.id, size)].append(
                        e.fluxion_impact_bps
                    )

            all_outcomes[pair.id][size] = {}
            for lag_ms in lag_ms_list:
                outs = outcomes_for_lag(
                    entries,
                    books=books,
                    depths=depths,
                    metrics_cfg=metrics_cfg,
                    lag_ms=lag_ms,
                    align_ms=align_ms,
                    withdraw_fee_usd=withdraw_fee,
                    pair_id=pair.id,
                )
                by_sess: dict[str, list[SequentialOutcome]] = defaultdict(list)
                for o in outs:
                    by_sess[o.session].append(o)
                all_outcomes[pair.id][size][lag_ms] = dict(by_sess)

    conn.close()

    # --- Distribution tables (primary size) ---
    dist_open: list[dict[str, Any]] = []
    dist_closed: list[dict[str, Any]] = []
    for pair_id, by_size in sorted(all_outcomes.items()):
        for lag_ms in lag_ms_list:
            for sess, dest in (("open", dist_open), ("closed", dist_closed)):
                outs = by_size.get(PRIMARY_SIZE, {}).get(lag_ms, {}).get(sess, [])
                if not outs:
                    continue
                bps_s = distribution_stats([o.realised_bps for o in outs])
                usd_s = distribution_stats([o.realised_pnl_usd for o in outs])
                dest.append(
                    {
                        "pair_id": pair_id,
                        "lag_min": lag_ms // 60_000,
                        "n": bps_s.n,
                        "win_rate": _fmt_pct(bps_s.win_rate),
                        "mean_bps": _fmt_dec(bps_s.mean, 2),
                        "median_bps": _fmt_dec(bps_s.median, 2),
                        "p5_bps": _fmt_dec(bps_s.p5, 2),
                        "p25_bps": _fmt_dec(bps_s.p25, 2),
                        "p75_bps": _fmt_dec(bps_s.p75, 2),
                        "p95_bps": _fmt_dec(bps_s.p95, 2),
                        "worst_bps": _fmt_dec(bps_s.worst, 2),
                        "mean_usd": _fmt_dec(usd_s.mean, 4),
                        "stats_bps": bps_s.to_dict(),
                        "stats_usd": usd_s.to_dict(),
                    }
                )

    def _k_cell(fit: Any) -> str:
        # Only surface k when the target was actually reached.
        if not fit.reachable or fit.k is None:
            return "n/a"
        return _fmt_dec(fit.k, 2)

    # --- drift_premium_k at primary ---
    # Fit on fire-on-open outcomes (decision-aligned). When n is thin, lower
    # min_admitted to 3 for a provisional fit (flagged in the table).
    drift_rows: list[dict[str, Any]] = []
    recommended_k: dict[str, Any] = {}
    for pair_id, by_size in sorted(all_outcomes.items()):
        for sess in ("open", "closed"):
            outs = (
                by_size.get(PRIMARY_SIZE, {})
                .get(primary_lag_ms, {})
                .get(sess, [])
            )
            min_adm = 5 if len(outs) >= 5 else 3
            sig = sigma_lookup.get((pair_id, sess, primary_lag_ms))
            sigma_src = sess
            if sig is None:
                sig = sigma_lookup.get((pair_id, "all", primary_lag_ms), Decimal(0))
                sigma_src = "all" if sig else sess
            fits = fit_drift_premium_k(
                outs,
                sigma_bps=sig or Decimal(0),
                targets=DEFAULT_WIN_RATE_TARGETS,
                min_admitted=min_adm,
            )
            by_t = {f.target_win_rate: f for f in fits}
            empty = DriftPremiumFit(
                target_win_rate=Decimal("0"),
                k=None,
                n_admitted=0,
                realised_win_rate=None,
                sigma_bps=sig or Decimal(0),
                reachable=False,
            )
            f90 = by_t.get(Decimal("0.90"), empty)
            f95 = by_t.get(Decimal("0.95"), empty)
            f99 = by_t.get(Decimal("0.99"), empty)
            reachable_tags = [
                t
                for t, f in (("90", f90), ("95", f95), ("99", f99))
                if f.reachable
            ]
            drift_rows.append(
                {
                    "pair_id": pair_id,
                    "session": sess,
                    "sigma_bps": _fmt_dec(sig, 2),
                    "sigma_session": sigma_src,
                    "k90": _k_cell(f90),
                    "wr90": _fmt_pct(f90.realised_win_rate),
                    "n90": f90.n_admitted,
                    "k95": _k_cell(f95),
                    "wr95": _fmt_pct(f95.realised_win_rate),
                    "n95": f95.n_admitted,
                    "k99": _k_cell(f99),
                    "wr99": _fmt_pct(f99.realised_win_rate),
                    "n99": f99.n_admitted,
                    "reachable": (
                        "90/95/99"
                        if len(reachable_tags) == 3
                        else ("+".join(reachable_tags) or "no")
                    ),
                    "min_admitted": min_adm,
                    "fits": [f.to_dict() for f in fits],
                }
            )
            if sess == "open" and f90.reachable and f90.k is not None:
                recommended_k[pair_id] = {
                    "k90": str(f90.k),
                    "sigma_bps": str(sig or 0),
                    "threshold_bps": str(
                        (f90.k * (sig or 0)).quantize(Decimal("0.01"))
                    ),
                }

    # --- Clip sweep (open primary; closed diagnostic) ---
    clip_rows: list[dict[str, Any]] = []
    clip_opt: list[dict[str, Any]] = []
    clip_opt_closed: list[dict[str, Any]] = []
    for pair_id, by_size in sorted(all_outcomes.items()):
        for sess, opt_dest in (("open", clip_opt), ("closed", clip_opt_closed)):
            by_size_outs: dict[Decimal, list[SequentialOutcome]] = {}
            impact_map: dict[Decimal, Decimal] = {}
            for size in CLIP_SIZES:
                outs = by_size.get(size, {}).get(primary_lag_ms, {}).get(sess, [])
                by_size_outs[size] = outs
                if sess == "open":
                    impacts = impact_by_pair_size.get((pair_id, size), [])
                    if impacts:
                        impact_map[size] = sum(impacts, start=Decimal(0)) / Decimal(
                            len(impacts)
                        )
            rows = clip_size_sweep(
                by_size_outs,
                withdraw_fee_usd=withdraw_fee,
                impact_bps_by_size=impact_map if sess == "open" else None,
            )
            opt = optimal_clip_size(rows)
            if sess == "open":
                for r in rows:
                    is_opt = opt is not None and r.size_usd == opt.size_usd
                    clip_rows.append(
                        {
                            "pair_id": pair_id,
                            "size_usd": str(int(r.size_usd)),
                            "n": r.n,
                            "mean_bps": _fmt_dec(r.mean_realised_bps, 2),
                            "median_bps": _fmt_dec(r.median_realised_bps, 2),
                            "mean_usd": _fmt_dec(r.mean_realised_usd, 4),
                            "win_rate": _fmt_pct(r.win_rate),
                            "fee_bps": _fmt_dec(r.withdraw_fee_bps, 2),
                            "impact_bps": _fmt_dec(r.mean_fluxion_impact_bps, 2),
                            "optimum": "✓" if is_opt else "",
                        }
                    )
            if opt is not None:
                note = "provisional (n<5)" if opt.n < 5 else ""
                if sess == "closed":
                    note = (note + "; closed diagnostic").strip("; ")
                opt_dest.append(
                    {
                        "pair_id": pair_id,
                        "size_usd": str(int(opt.size_usd)),
                        "n": opt.n,
                        "mean_usd": _fmt_dec(opt.mean_realised_usd, 4),
                        "mean_bps": _fmt_dec(opt.mean_realised_bps, 2),
                        "win_rate": _fmt_pct(opt.win_rate),
                        "note": note,
                    }
                )
            else:
                opt_dest.append(
                    {
                        "pair_id": pair_id,
                        "size_usd": "n/a",
                        "n": 0,
                        "mean_usd": "n/a",
                        "mean_bps": "n/a",
                        "win_rate": "n/a",
                        "note": "no positive-mean size",
                    }
                )

    # --- Universe + portfolio ---
    # Sequential capital lock = primary lag (legs are not simultaneous).
    seq_flight_ms = primary_lag_ms
    universe: list[dict[str, Any]] = []
    seq_open_all: list[SequentialOutcome] = []
    sim_windows_for_portfolio: list[OpportunityWindow] = []

    go_syms: list[str] = []
    core_syms: list[str] = []
    for pair_id, by_size in sorted(all_outcomes.items()):
        for sess in ("open", "closed"):
            outs = (
                by_size.get(PRIMARY_SIZE, {})
                .get(primary_lag_ms, {})
                .get(sess, [])
            )
            if not outs:
                continue
            # Sequential: admit on sim edge; book realised (incl. losses);
            # flight = transit lag. Per-symbol series still uses day cooldown
            # so re-entry is one trade / day after the lag.
            seq_profit = sequential_capturable_profit(
                outs,
                reentry_cooldown_ms=reentry_ms,
                trade_duration_ms=seq_flight_ms,
                min_simultaneous_edge_bps=Decimal(0),
            )
            # Simultaneous baseline: paper edge known at entry (positive only
            # is legitimate — decision-time). Flight stays short (trade_ms).
            wins = [
                OpportunityWindow(
                    pair_id=pair_id,
                    direction=DIRECTION,
                    session=sess,  # type: ignore[arg-type]
                    venue="amm",
                    size_usd=PRIMARY_SIZE,
                    start_ms=o.entry_ts_ms,
                    end_ms=o.entry_ts_ms,
                    n_samples=1,
                    trade_pnl_usd=o.simultaneous_pnl_usd,
                    peak_edge_bps=o.simultaneous_edge_bps,
                )
                for o in outs
                if o.simultaneous_pnl_usd > 0
            ]
            sim_profit = capturable_profit_single_flight(
                wins,
                reentry_cooldown_ms=reentry_ms,
                trade_duration_ms=trade_ms,
                max_trade_usd=MAX_TRADE_USD,
            )
            seq_pd = seq_profit / days
            sim_pd = sim_profit / days
            wr = distribution_stats([o.realised_pnl_usd for o in outs]).win_rate
            clears = seq_pd >= Decimal(1)
            universe.append(
                {
                    "pair_id": pair_id,
                    "session": sess,
                    "sim_per_day": _fmt_dec(sim_pd, 4),
                    "seq_per_day": _fmt_dec(seq_pd, 4),
                    "win_rate": _fmt_pct(wr),
                    "n_windows": len(outs),
                    "clears": "yes" if clears else "no",
                    "seq_per_day_raw": str(seq_pd),
                    "sim_per_day_raw": str(sim_pd),
                }
            )
            if sess == "open":
                seq_open_all.extend(outs)
                sim_windows_for_portfolio.extend(wins)
                if seq_pd > 0:
                    go_syms.append(pair_id)
                if clears:
                    core_syms.append(pair_id)

    seq_port = portfolio_sequential_profit(
        seq_open_all,
        trade_duration_ms=seq_flight_ms,
        min_simultaneous_edge_bps=Decimal(0),
    )

    sim_port = portfolio_capturable_profit(
        sim_windows_for_portfolio,
        reentry_cooldown_ms=reentry_ms,
        trade_duration_ms=trade_ms,
        max_trade_usd=MAX_TRADE_USD,
        inventory_usd=INVENTORY_USD,
    )
    seq_pd_port = seq_port / days
    sim_pd_port = sim_port / days
    ratio = (
        seq_pd_port / sim_pd_port if sim_pd_port > 0 else Decimal(0)
    )

    if seq_pd_port >= GO_USDT_PER_DAY and len(go_syms) >= GO_MIN_SYMBOLS:
        verdict = "GO (sequential)"
        detail = (
            f"Sequential portfolio **{_fmt_dec(seq_pd_port)} USDT/day** meets "
            f"the ≥{GO_USDT_PER_DAY} gate with **{len(go_syms)}** symbols showing "
            f"positive sequential open-session profit "
            f"({', '.join(go_syms) if go_syms else '—'}). "
            f"Simultaneous baseline was {_fmt_dec(sim_pd_port)} USDT/day "
            f"(ratio {_fmt_dec(ratio, 3)}). "
            "Live trading still requires the engine to apply "
            "`drift_premium_k · σ` admission."
        )
    elif seq_pd_port < NOGO_USDT_PER_DAY:
        verdict = "NO-GO (sequential)"
        detail = (
            f"Sequential portfolio **{_fmt_dec(seq_pd_port)} USDT/day** is below "
            f"the {NOGO_USDT_PER_DAY} no-go floor. Simultaneous baseline was "
            f"{_fmt_dec(sim_pd_port)} USDT/day. Do not live-trade on this span."
        )
    else:
        verdict = "MARGINAL (sequential)"
        detail = (
            f"Sequential portfolio **{_fmt_dec(seq_pd_port)} USDT/day** sits "
            f"between no-go ({NOGO_USDT_PER_DAY}) and go ({GO_USDT_PER_DAY}). "
            f"Symbols with positive sequential open profit: "
            f"{', '.join(go_syms) if go_syms else '—'}. "
            "Re-run after more clean RTH sessions before committing capital."
        )

    # --- Lag sensitivity ---
    # Two columns of honesty: (1) all windows that resolve at that lag (n may
    # shrink with N — survivorship), and (2) paired with primary lag only
    # (intersection of primary and that lag) so degradation is comparable.
    lag_sens: list[dict[str, Any]] = []
    primary_keys: set[tuple[str, int]] = set()
    for pair_id, by_size in all_outcomes.items():
        for o in by_size.get(PRIMARY_SIZE, {}).get(primary_lag_ms, {}).get(
            "open", []
        ):
            primary_keys.add((pair_id, o.entry_ts_ms))

    for lag_ms in lag_ms_list:
        pool_all: list[SequentialOutcome] = []
        pool_paired: list[SequentialOutcome] = []
        for pair_id, by_size in all_outcomes.items():
            for o in by_size.get(PRIMARY_SIZE, {}).get(lag_ms, {}).get("open", []):
                pool_all.append(o)
                if (pair_id, o.entry_ts_ms) in primary_keys:
                    pool_paired.append(o)
        if not pool_all:
            continue

        def _port_stats(
            pool: list[SequentialOutcome], flight: int
        ) -> dict[str, Any]:
            if not pool:
                return {
                    "portfolio_per_day": "n/a",
                    "win_rate": "n/a",
                    "mean_bps": "n/a",
                    "n": 0,
                }
            port = portfolio_sequential_profit(
                pool,
                trade_duration_ms=flight,
                min_simultaneous_edge_bps=Decimal(0),
            )
            st = distribution_stats([o.realised_bps for o in pool])
            wr = distribution_stats([o.realised_pnl_usd for o in pool]).win_rate
            return {
                "portfolio_per_day": _fmt_dec(port / days, 4),
                "win_rate": _fmt_pct(wr),
                "mean_bps": _fmt_dec(st.mean, 2),
                "n": len(pool),
            }

        raw = _port_stats(pool_all, lag_ms)
        paired = _port_stats(pool_paired, lag_ms)
        lag_sens.append(
            {
                "lag_min": lag_ms // 60_000,
                "portfolio_per_day": raw["portfolio_per_day"],
                "win_rate": raw["win_rate"],
                "mean_bps": raw["mean_bps"],
                "n": raw["n"],
                "paired_n": paired["n"],
                "paired_portfolio_per_day": paired["portfolio_per_day"],
                "paired_win_rate": paired["win_rate"],
                "paired_mean_bps": paired["mean_bps"],
            }
        )

    lag_note = (
        "Each lag reports (a) **all** open windows that resolve at that lag "
        "(n may shrink — missing delayed books) and (b) the **paired** subset "
        f"also present at the primary lag N={PRIMARY_LAG_MIN} (fair degradation). "
        "Portfolio flight = lag. If N=30/60 paired $/day collapses, set "
        "`deposit_timeout_s` inside the still-viable horizon (bot DESIGN §2.8)."
    )

    # Recommend k from open-session fits: median of reachable k90
    k90_vals = [
        Decimal(v["k90"])
        for v in recommended_k.values()
    ]
    if k90_vals:
        k90_vals_sorted = sorted(k90_vals)
        med_k = k90_vals_sorted[len(k90_vals_sorted) // 2]
        drift_note = (
            f"Recommended starting point for bot config: per-symbol open-session "
            f"`drift_premium_k` at 90% target (see table); cross-symbol median "
            f"k@90% ≈ **{_fmt_dec(med_k, 2)}**. Prefer per-symbol values when "
            f"σ differs materially. 99% targets are often unreachable on this "
            f"thin span — treat as aspirational until more RTH hours land."
        )
    else:
        med_k = None
        # Surface any closed-session fits for diagnostics only.
        closed_ks = [
            r
            for r in drift_rows
            if r["session"] == "closed" and r["k90"] != "n/a"
        ]
        closed_note = ""
        if closed_ks:
            bits = ", ".join(
                f"{r['pair_id']} k90={r['k90']} (n={r['n90']})" for r in closed_ks
            )
            closed_note = (
                f" Closed-session fits exist for diagnostics only ({bits}) — "
                f"**do not** use them for RTH admission."
            )
        drift_note = (
            "**This span derives no open-session `drift_premium_k`.** No "
            "open symbol reached a 90% realised win-rate target on the k-grid "
            "with enough admitted samples. The bot must not live-trade until "
            "a re-run with thicker RTH produces reachable open-session k "
            f"values (or an explicit owner waiver).{closed_note}"
        )

    universe_note = (
        f"Sequential vs simultaneous on this span: simultaneous portfolio "
        f"{_fmt_dec(sim_pd_port)} → sequential {_fmt_dec(seq_pd_port)} USDT/day "
        f"(ratio {_fmt_dec(ratio, 3)}). Sequential books **all** admitted "
        f"cycles' realised PnL (losses included) with flight = lag "
        f"({PRIMARY_LAG_MIN} min); simultaneous books decision-time paper "
        f"edge with a short flight. Direction 1 only, primary clip "
        f"${PRIMARY_SIZE}, fire-on-open windows — not the M0 two-direction "
        f"$1k headline. Compare methodology carefully against "
        f"`m8-xstocks-edge-quant.md`."
    )

    caveats = [
        (
            f"Study window {_ms_iso(since_ms)} → {_ms_iso(until_ms)} "
            f"({_fmt_dec(calendar_days, 3)} calendar days). "
            f"RTH hours in window: {rth_h:.2f} h (excl. collector_down: "
            f"{rth_ok:.2f} h). Closed: {closed_h:.2f} h (excl. gap: "
            f"{closed_ok:.2f} h). Collector downtime: {gap_hours:.2f} h."
        ),
        (
            f"Flat withdraw fee assumed **${withdraw_fee}** per cycle "
            "(bot DESIGN §2.3 / §3.1 A+D). Authenticated fee pull (WHI-907) "
            "should replace this constant when it lands."
        ),
        (
            "Bybit sell at t+N is a paper VWAP with the same depth model as "
            "PnL v2 — not a live fill. Thin books and crediting delays can "
            "be worse than measured."
        ),
        (
            "Direction 2 (`buy_bybit_sell_fluxion`) is out of scope (needs "
            "~15 min Bybit xStock withdrawal before the DEX leg)."
        ),
        (
            "No corporate-action calendar applied; re-runs over longer spans "
            "must disclose excluded days."
        ),
        (
            "Fit quality is limited by raw retention (~2d book/depth). "
            "Re-run after ≥5 clean RTH sessions before locking production k."
        ),
    ]

    bot_consume = [
        (
            "Admission: `edge_bps ≥ min_edge_bps[session][direction]` **and** "
            "`edge_bps ≥ drift_premium_k[symbol] × sigma_transit_bps[symbol, N]` "
            f"with N≈{PRIMARY_LAG_MIN} min — **but this span produced no "
            "open-session k**; block live trading until a re-run supplies one."
        ),
        (
            f"Clip size: per-symbol optimum from the sweep (often below "
            f"${MAX_TRADE_USD}); flat fee pushes up, Fluxion impact + transit "
            "premium push down."
        ),
        (
            "Symbol universe: open-session sequential go-list above; drop "
            "symbols whose sequential $/day is ~0 even before the premium."
        ),
        (
            "Deposit timeout: set from the lag-sensitivity table so that "
            "waiting inside the timeout remains positive-EV on average; "
            "beyond that, take the exposure-breaker path."
        ),
        (
            "Update bot `docs/DESIGN.md` §8 delay-decay bullet to cite this "
            "report (companion commit in mantle-stocks-arbitrage-bots)."
        ),
    ]

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "regen_cmd": (
            f"uv run python scripts/xstocks_delay_decay.py "
            f"--db data/monitor-bybit-fluxion.db --sample-ms {sample_ms}"
        ),
        "method": {
            "market": "bybit-fluxion",
            "direction": DIRECTION,
            "engine": "pnl_v2 + delayed Bybit sell",
            "analysis": "monitor.analysis.delay_decay",
            "clip_sizes_usd": ", ".join(str(int(s)) for s in CLIP_SIZES),
            "lags_min": ", ".join(str(m) for m in LAG_MINUTES),
            "primary_lag_min": PRIMARY_LAG_MIN,
            "primary_size_usd": str(int(PRIMARY_SIZE)),
            "withdraw_fee_usd": str(withdraw_fee),
            "sample_ms": sample_ms,
            "align_ms": align_ms,
            "reentry_cooldown_ms": reentry_ms,
            "trade_duration_ms": trade_ms,
            "max_gap_ms": max_gap_ms,
            "pricing_anomaly_gate": (
                str(max_abs) if max_abs is not None else "off"
            ),
            "sigma_align_ms": "max(align_ms, sample_ms)",
        },
        "data_span_note": (
            f"Study window wall clock: **{_ms_iso(since_ms)} → {_ms_iso(until_ms)}** "
            f"({_fmt_dec(calendar_days, 3)} d). "
            f"RTH excl. gap: **{rth_ok:.2f} h**. "
            f"Delayed sells may resolve up to {max(LAG_MINUTES)} min past the "
            "book max timestamp."
        ),
        "spans": [
            {
                "table": s.table,
                "n_rows": s.n_rows,
                "min_utc": _ms_iso(s.min_ms),
                "max_utc": _ms_iso(s.max_ms),
            }
            for s in spans_raw.values()
        ],
        "headline": {
            "calendar_days": _fmt_dec(calendar_days, 3),
            "primary_lag_min": PRIMARY_LAG_MIN,
            "primary_size_usd": str(int(PRIMARY_SIZE)),
            "withdraw_fee_usd": str(withdraw_fee),
            "sim_portfolio_per_day": _fmt_dec(sim_pd_port, 4),
            "seq_portfolio_per_day": _fmt_dec(seq_pd_port, 4),
            "seq_over_sim_ratio": _fmt_dec(ratio, 3),
            "go_list": ", ".join(go_syms) if go_syms else "",
            "core_go_list": ", ".join(core_syms) if core_syms else "",
            "verdict": verdict,
            "verdict_detail": detail,
            "median_k90": _fmt_dec(med_k, 2) if med_k is not None else None,
            "recommended_k_by_symbol": recommended_k,
        },
        "transit_sigma": transit_rows,
        "dist_open": dist_open,
        "dist_closed": dist_closed,
        "drift_premium_k": drift_rows,
        "drift_premium_note": drift_note,
        "clip_sweep": clip_rows,
        "clip_optimum": clip_opt,
        "clip_optimum_closed": clip_opt_closed,
        "universe": universe,
        "universe_note": universe_note,
        "lag_sensitivity": lag_sens,
        "lag_sensitivity_note": lag_note,
        "caveats": caveats,
        "bot_consume": bot_consume,
        "elapsed_s": round(time.time() - t0, 1),
    }

    report_path = Path(args.report)
    json_path = Path(args.json_out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    report_path.write_text(build_report(payload), encoding="utf-8")
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {report_path} and {json_path} in {payload['elapsed_s']}s — "
        f"verdict={verdict}",
        flush=True,
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        default=str(_REPO / "data" / "monitor-bybit-fluxion.db"),
        help="Path to bybit-fluxion journal SQLite",
    )
    p.add_argument("--sample-ms", type=int, default=DEFAULT_SAMPLE_MS)
    p.add_argument("--align-ms", type=int, default=DEFAULT_ALIGN_MS)
    p.add_argument("--max-gap-ms", type=int, default=DEFAULT_MAX_GAP_MS)
    p.add_argument(
        "--reentry-cooldown-ms", type=int, default=DEFAULT_REENTRY_COOLDOWN_MS
    )
    p.add_argument(
        "--trade-duration-ms", type=int, default=DEFAULT_TRADE_DURATION_MS
    )
    p.add_argument(
        "--withdraw-fee-usd",
        type=str,
        default=str(DEFAULT_WITHDRAW_FEE_USD),
        help="Flat recycle withdraw fee USD per cycle (A+D)",
    )
    p.add_argument("--report", default=str(DEFAULT_REPORT))
    p.add_argument("--json-out", default=str(DEFAULT_JSON))
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
