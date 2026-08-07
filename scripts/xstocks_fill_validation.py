#!/usr/bin/env python3
"""WHI-908 / M8: on-chain fill validation of paper dislocation windows.

Rebuilds AMM $1,000 opportunity windows with the same loaders / PnL v2 path as
``xstocks_edge_quant.py`` (M0), ranks the top windows by fire-on-open capturable
profit, matches each against journal ``fluxion_swaps``, and classifies:

  taken | untaken_with_liquidity | untaken_too_thin

Also reports as-of pool-join staleness for untaken windows and the fraction of
portfolio headline profit retained under tighter ``pricing_anomaly`` gates
(500 / 300 / 200 / 100 bps).

Usage (repo root; journal must exist):

  uv run python scripts/xstocks_fill_validation.py
  uv run python scripts/xstocks_fill_validation.py --db data/monitor-bybit-fluxion.db
"""

from __future__ import annotations

import argparse
import importlib.util
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
    detect_windows,
    portfolio_capturable_profit,
)
from monitor.analysis.fill_validation import (  # noqa: E402
    DecodedSwapView,
    abs_basis_bps,
    classify_window,
    effective_fill_price,
    pool_age_stats,
    swap_notional_usd,
    swaps_in_window,
)
from monitor.markets import load_market_context  # noqa: E402
from monitor.metrics.amm_pool import (  # noqa: E402
    AmmPoolState,
    amm_pool_from_pair_tick,
    quote_is_token0_for_pair,
)
from monitor.metrics.amm_quote import amm_quote_for_cex  # noqa: E402
from monitor.metrics.edge import mid_from_bid_ask  # noqa: E402
from monitor.metrics.pnl_snapshot import levels_from_depth_curve  # noqa: E402
from monitor.metrics.pnl_v2 import compute_pnl_usd  # noqa: E402
from monitor.metrics.session import session_kind  # noqa: E402
from monitor.quotes import (  # noqa: E402
    BybitBookTick,
    BybitDepthTick,
    FluxionPoolStateTick,
)
from monitor.storage.reader import _row_to_swap  # noqa: E402
from monitor.symbols.models import Pair  # noqa: E402

# `_row_to_swap` is module-private on JournalReader but is the only typed
# SQLite→tick path; research scripts reuse it (same as xstocks_edge_quant.py).

# Load sibling M0 script for shared journal loaders (same path as M0).
_EQ_PATH = _REPO / "scripts" / "xstocks_edge_quant.py"
_eq_spec = importlib.util.spec_from_file_location("xstocks_edge_quant", _EQ_PATH)
if _eq_spec is None or _eq_spec.loader is None:
    raise RuntimeError(f"cannot load {_EQ_PATH}")
_eq = importlib.util.module_from_spec(_eq_spec)
# Required before exec_module so @dataclass can resolve cls.__module__.
sys.modules[_eq_spec.name] = _eq
_eq_spec.loader.exec_module(_eq)

SIZE_USD = Decimal(1000)
INVENTORY_USD = Decimal(5000)
MAX_TRADE_USD = Decimal(1000)
DIRECTIONS = ("buy_fluxion_sell_bybit", "buy_bybit_sell_fluxion")
DEFAULT_SAMPLE_MS = 20_000
DEFAULT_ALIGN_MS = 15_000
DEFAULT_MAX_GAP_MS = 120_000
DEFAULT_TRADE_DURATION_MS = 5_000
DEFAULT_REENTRY_COOLDOWN_MS = 86_400_000
DEFAULT_TOP_N = 20
ANOMALY_GATES_BPS = (Decimal(500), Decimal(300), Decimal(200), Decimal(100))
DEFAULT_REPORT = _REPO / "docs" / "references" / "m8-onchain-fill-validation.md"
DEFAULT_JSON = _REPO / "docs" / "references" / "m8-onchain-fill-validation.json"


@dataclass(frozen=True, slots=True)
class SampleMeta:
    """Per-sample join diagnostics for staleness / anomaly sensitivity."""

    ts_ms: int
    pair_id: str
    direction: str
    session: str
    pool_age_ms: int
    abs_basis_bps: Decimal | None
    pool: AmmPoolState
    pool_tick: FluxionPoolStateTick


def _ms_iso(ms: int | None) -> str:
    if ms is None:
        return "n/a"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt(v: Decimal | float | int | None, places: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, Decimal):
        q = Decimal(10) ** -places
        return str(v.quantize(q))
    return f"{v:.{places}f}"


def build_amm_samples_with_meta(
    *,
    pair: Pair,
    books: Sequence[BybitBookTick],
    pools: Sequence[FluxionPoolStateTick],
    depths: Sequence[BybitDepthTick],
    metrics_cfg: Any,
    quote_decimals: int,
    size_usd: Decimal,
    gaps: Sequence[Any],
    align_ms: int,
    max_abs_spread_bps: Decimal | None,
) -> tuple[list[EdgeSample], list[SampleMeta]]:
    """Like M0 ``build_amm_samples`` but also returns join-age / basis meta."""
    if not books or not pools:
        return [], []
    pool_ts = [p.recv_ts_ms for p in pools]
    depth_ts = [d.recv_ts_ms for d in depths]
    samples: list[EdgeSample] = []
    metas: list[SampleMeta] = []
    for book in books:
        ts = book.recv_ts_ms
        if _eq._in_gap(ts, gaps):
            continue
        pi = _eq._as_of_idx(pool_ts, ts)
        if pi is None:
            continue
        pool_tick = pools[pi]
        pool_age = ts - pool_tick.recv_ts_ms
        if pool_age > align_ms:
            continue
        amm = amm_pool_from_pair_tick(pair, pool_tick, quote_decimals=quote_decimals)
        if amm is None:
            continue
        cex_mid = mid_from_bid_ask(book.bid_de_multiplied, book.ask_de_multiplied)
        if cex_mid is None or cex_mid <= 0:
            continue
        _, reason = amm_quote_for_cex(
            pool_tick,
            cex_mid=cex_mid,
            max_abs_spread_bps=max_abs_spread_bps,
        )
        if reason is not None:
            continue
        bids = asks = None
        di = _eq._as_of_idx(depth_ts, ts) if depth_ts else None
        if di is not None and ts - depths[di].recv_ts_ms <= align_ms:
            bids = levels_from_depth_curve(depths[di], side="bid") or None
            asks = levels_from_depth_curve(depths[di], side="ask") or None
        sess = session_kind(
            datetime.fromtimestamp(ts / 1000, tz=UTC), config=metrics_cfg
        ).value
        basis = abs_basis_bps(amm_mid=pool_tick.mid_usdc_per_native, cex_mid=cex_mid)
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
            samples.append(
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
            metas.append(
                SampleMeta(
                    ts_ms=ts,
                    pair_id=pair.id,
                    direction=direction,
                    session=sess,
                    pool_age_ms=pool_age,
                    abs_basis_bps=basis,
                    pool=amm,
                    pool_tick=pool_tick,
                )
            )
    return samples, metas


def load_swaps(
    conn: sqlite3.Connection,
    pair_id: str,
    *,
    since_ms: int,
    until_ms: int,
    token0_is_quote: bool,
) -> list[DecodedSwapView]:
    """Load journal swaps in ``[since_ms, until_ms]`` (recv time).

    Token order must be known so notional and fill price use the quote leg
    correctly — callers derive it from pool state for the pair.
    """
    rows = conn.execute(
        """
        SELECT * FROM fluxion_swaps
        WHERE pair_id = ?
          AND recv_ts_ms >= ?
          AND recv_ts_ms <= ?
          AND gap = 0
        ORDER BY recv_ts_ms ASC, log_index ASC
        """,
        (pair_id, since_ms, until_ms),
    ).fetchall()
    out: list[DecodedSwapView] = []
    for row in rows:
        tick = _row_to_swap(row)
        notional = swap_notional_usd(
            amount_token0=tick.amount_token0,
            amount_token1=tick.amount_token1,
            token0_is_quote=token0_is_quote,
            price_usdc_per_wrapper=tick.price_usdc_per_wrapper,
        )
        fill_px = effective_fill_price(
            amount_token0=tick.amount_token0,
            amount_token1=tick.amount_token1,
            token0_is_quote=token0_is_quote,
        )
        out.append(
            DecodedSwapView(
                pair_id=tick.pair_id,
                recv_ts_ms=tick.recv_ts_ms,
                block_number=tick.block_number,
                block_ts=tick.block_ts,
                tx_hash=tick.tx_hash,
                log_index=tick.log_index,
                direction=tick.direction,
                amount_token0=tick.amount_token0,
                amount_token1=tick.amount_token1,
                price_usdc_per_wrapper=tick.price_usdc_per_wrapper,
                notional_usd=notional,
                effective_price=fill_px,
            )
        )
    return out


def _meta_key(pair_id: str, direction: str, session: str) -> tuple[str, str, str]:
    return (pair_id, direction, session)


def _window_metas(
    metas_by_key: dict[tuple[str, str, str], list[SampleMeta]],
    w: OpportunityWindow,
) -> list[SampleMeta]:
    key = _meta_key(w.pair_id, w.direction, w.session)
    return [
        m for m in metas_by_key.get(key, []) if w.start_ms <= m.ts_ms <= w.end_ms
    ]


def _pool_as_of(
    pools: Sequence[FluxionPoolStateTick],
    pair: Pair,
    *,
    ts_ms: int,
    quote_decimals: int,
    max_age_ms: int | None = None,
) -> tuple[AmmPoolState | None, FluxionPoolStateTick | None, int | None]:
    """As-of pool at ``ts_ms``; optionally refuse joins older than ``max_age_ms``."""
    if not pools:
        return None, None, None
    pool_ts = [p.recv_ts_ms for p in pools]
    pi = _eq._as_of_idx(pool_ts, ts_ms)
    if pi is None:
        return None, None, None
    tick = pools[pi]
    age = ts_ms - tick.recv_ts_ms
    if max_age_ms is not None and age > max_age_ms:
        return None, tick, age
    amm = amm_pool_from_pair_tick(pair, tick, quote_decimals=quote_decimals)
    return amm, tick, age


def _swap_row_dict(sw: DecodedSwapView) -> dict[str, Any]:
    return {
        "recv": _ms_iso(sw.recv_ts_ms),
        "recv_ts_ms": sw.recv_ts_ms,
        "block_number": sw.block_number,
        "block_ts": sw.block_ts,
        "tx_hash": sw.tx_hash,
        "log_index": sw.log_index,
        "direction": sw.direction,
        "notional_usd": _fmt(sw.notional_usd),
        "effective_price": (
            _fmt(sw.effective_price) if sw.effective_price is not None else "—"
        ),
        "post_swap_mid": (
            _fmt(sw.price_usdc_per_wrapper)
            if sw.price_usdc_per_wrapper is not None
            else "—"
        ),
        "amount_token0": str(sw.amount_token0),
        "amount_token1": str(sw.amount_token1),
    }


def render_report(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    a = lines.append
    m = payload["method"]
    h = payload["headline"]
    a("# On-chain fill validation of M8 dislocation windows (WHI-908)")
    a("")
    a(
        "Did anyone actually swap during the large paper dislocation windows that "
        "drive the M8 go-case — or are those windows measurement artifacts?"
    )
    a("")
    a(f"**Generated:** {payload['generated_utc']}")
    a("")
    a("## Regeneration")
    a("")
    a("```bash")
    a(payload["repro_command"])
    a("```")
    a("")
    a(
        "Pure helpers: `monitor.analysis.fill_validation` (unit-tested). "
        "Windows / PnL: same path as M0 (`monitor.analysis.edge_quant` + "
        "`monitor.metrics.pnl_v2.compute_pnl_usd`)."
    )
    a("")
    a("## Method")
    a("")
    a("| Item | Value |")
    a("|------|--------|")
    for k, v in m.items():
        a(f"| {k} | {v} |")
    a("")
    a("### Classification")
    a("")
    a(
        "- **taken** — ≥1 on-chain `fluxion_swaps` row in the profitable AMM "
        "direction inside the window (`buy_fluxion_sell_bybit` → `buy_native`, "
        "`buy_bybit_sell_fluxion` → `sell_native`)."
    )
    a(
        "- **untaken_with_liquidity** — no matching swap, but UniV3 single-range "
        "math fills the study notional ($1,000) at window-open pool state."
    )
    a(
        "- **untaken_too_thin** — no matching swap and the range cannot support "
        "$1,000 (paper opportunity never capturable at size)."
    )
    a("")
    a(
        "**Method note on class (c):** opportunity windows are built only from "
        "samples that already cleared the PnL v2 $1,000 AMM fillability gate "
        "(same UniV3 single-range path as the depth probe). Under this pipeline, "
        "`untaken_too_thin` is **empty by construction** for any window that has "
        "a surviving pool snapshot — a zero count is not independent evidence that "
        "depth was adequate in the wild, only that paper samples required it. A "
        "separate paper path that scores edge without the slip fill gate would be "
        "needed to populate class (c)."
    )
    a("")
    a("### Data span note (retention)")
    a("")
    a(payload["data_span_note"])
    a("")
    a("## Study span")
    a("")
    s = payload["study"]
    a(f"- Wall clock: **{s['start']} → {s['end']}** ({s['span_hours']:.2f} h)")
    a(f"- Calendar days: **{s['calendar_days']:.3f}**")
    a(
        f"- Journal swaps in span: **{s['n_swaps']}** "
        f"(across {s['n_swap_pairs']} pairs)"
    )
    a("")
    a("## Headline (portfolio single-flight, AMM $1,000, T=0)")
    a("")
    a("| Metric | Value |")
    a("|--------|------:|")
    a(f"| Portfolio capturable profit (total) | {h['profit_total_usd']} USDT |")
    a(f"| Average capturable profit / day | {h['profit_per_day_usd']} USDT/day |")
    a(f"| N windows (all symbols) | {h['n_windows']} |")
    a(
        f"| Top-{h['top_n']} raw trade-PnL sum / portfolio "
        f"| {h['top_n_profit_share_pct']}%¹ |"
    )
    a(f"| Top-{h['top_n']} profit that is **taken** | {h['taken_profit_usd']} USDT |")
    a(
        f"| Share of portfolio profit validated by real fills "
        f"| **{h['validated_share_of_portfolio_pct']}%**² |"
    )
    a(
        f"| Share of top-{h['top_n']} profit validated by real fills "
        f"| **{h['validated_share_of_top_n_pct']}%** |"
    )
    a("")
    a(
        "¹ Top-N sums each window's fire-on-open `trade_pnl_usd` without portfolio "
        "single-flight, so the ratio can exceed 100% when high-PnL windows overlap "
        "in time. Portfolio profit (row 1) is the single-flight figure."
    )
    a(
        "² Numerator is the raw sum of taken windows' `trade_pnl_usd` (not "
        "single-flight-adjusted); denominator is portfolio single-flight profit."
    )
    a("")
    a("### Classification counts (top-N)")
    a("")
    a("| Class | Windows | Profit (USDT) | Share of top-N profit |")
    a("|-------|--------:|--------------:|----------------------:|")
    for row in h["class_counts"]:
        a(
            f"| {row['classification']} | {row['n']} | {row['profit_usd']} | "
            f"{row['share_pct']}% |"
        )
    a("")
    a("## Top windows")
    a("")
    a(
        "| # | Pair | Dir | Session | Start (UTC) | End (UTC) | "
        "Start blk† | End blk† | Dur (s) | Peak edge (bps) | Trade PnL | Class | "
        "Swaps (match/tot) | Virtual quote $‡ | Pool age med (ms) | Max abs basis (bps) |"
    )
    a(
        "|--:|------|-----|---------|-------------|-----------|"
        "---------:|--------:|--------:|----------------:|----------:|-------|"
        "-----------------:|----------------:|------------------:|-------------------:|"
    )
    for i, w in enumerate(payload["top_windows"], 1):
        a(
            f"| {i} | {w['pair_id']} | {w['direction_short']} | {w['session']} | "
            f"{w['start']} | {w['end']} | {w['start_block']} | {w['end_block']} | "
            f"{w['duration_s']} | {w['peak_edge_bps']} | "
            f"{w['trade_pnl_usd']} | **{w['classification']}** | "
            f"{w['n_swaps_matching']}/{w['n_swaps_total']} | "
            f"{w['virtual_quote_usd']} | {w['pool_age_median_ms']} | "
            f"{w['max_abs_basis_bps']} |"
        )
    a("")
    a(
        "† Start/end block = as-of `fluxion_pool_state.block_number` at the "
        "window edge (pool tick may lag open by ≤ align_ms). Swap matching uses "
        "`recv_ts_ms` only, not these blocks as a range."
    )
    a(
        "‡ Virtual quote $ = UniV3 virtual USDC-side reserves from (L, √P) — "
        "**not** tradeable depth; see `virtual_quote_side_usd` docstring. "
        "`Depth OK` is the single-range $1,000 fill probe."
    )
    a("")
    a("### Windows with on-chain swaps — full inventory")
    a("")
    a(
        "Every in-window swap (matching **and** opposite/unknown). "
        "`effective_price` is |quote|/|base| from the two legs; "
        "`post_swap_mid` is residual slot0 mid after the swap."
    )
    a("")
    with_swaps = [w for w in payload["top_windows"] if w["n_swaps_total"] > 0]
    if not with_swaps:
        a("_No top-N window contained any on-chain swap._")
        a("")
    else:
        for w in with_swaps:
            a(
                f"#### {w['pair_id']} {w['direction']} @ {w['start']} "
                f"(class **{w['classification']}**, PnL {w['trade_pnl_usd']} USDT, "
                f"blocks {w['start_block']}–{w['end_block']})"
            )
            a("")
            a(
                f"Profitable swap direction: `{w['profitable_swap_direction']}`. "
                f"{w['n_swaps_matching']} matching / {w['n_swaps_total']} total."
            )
            a("")
            a(
                "| recv (UTC) | block | tx | direction | notional $ | "
                "effective_price | post_swap_mid |"
            )
            a(
                "|------------|------:|----|-----------|-----------:|"
                "----------------:|--------------:|"
            )
            for sw in w["all_swaps"]:
                a(
                    f"| {sw['recv']} | {sw['block_number']} | "
                    f"`{sw['tx_hash'][:10]}…` | {sw['direction']} | "
                    f"{sw['notional_usd']} | {sw['effective_price']} | "
                    f"{sw['post_swap_mid']} |"
                )
            a("")
    a("### Untaken windows — as-of join staleness")
    a("")
    untaken = [
        w
        for w in payload["top_windows"]
        if w["classification"] != "taken"
    ]
    if not untaken:
        a("_All top-N windows were taken._")
        a("")
    else:
        a(
            "Pool age = book sample time − as-of `fluxion_pool_state.recv_ts_ms`. "
            f"Sample construction already drops joins older than align_ms="
            f"{m['align_ms']} ms, so ages reported here are **conditional on "
            f"passing that gate** — they cannot diagnose joins that were filtered "
            "out upstream. Ages near the gate still flag residual join risk; "
            "ages well below it mean the surviving samples used a fresh pool snapshot."
        )
        a("")
        a(
            "| Pair | Class | Start | Trade PnL | Age min | Age med | Age p90 | "
            "Age max | N samples | Max abs basis (bps) | Depth OK |"
        )
        a(
            "|------|-------|-------|----------:|--------:|--------:|--------:|"
            "--------:|----------:|--------------------:|---------:|"
        )
        for w in untaken:
            age = w["pool_age"]
            a(
                f"| {w['pair_id']} | {w['classification']} | {w['start']} | "
                f"{w['trade_pnl_usd']} | {age['min_ms']} | {age['median_ms']} | "
                f"{age['p90_ms']} | {age['max_ms']} | {age['n']} | "
                f"{w['max_abs_basis_bps']} | {w['depth_adequate']} |"
            )
        a("")
    a("## `pricing_anomaly` gate sensitivity")
    a("")
    a(
        "Rebuild all AMM $1,000 samples under tighter |AMM−CEX| gates and recompute "
        "portfolio single-flight capturable profit (same re-entry / trade duration "
        "as M0). Default gate in config is 500 bps."
    )
    a("")
    a("| Gate (bps) | N samples | N windows | Portfolio profit (USDT) | $/day | Retained vs 500 |")
    a("|----------:|----------:|----------:|------------------------:|------:|----------------:|")
    for row in payload["anomaly_sensitivity"]:
        a(
            f"| {row['gate_bps']} | {row['n_samples']} | {row['n_windows']} | "
            f"{row['profit_total_usd']} | {row['profit_per_day_usd']} | "
            f"{row['retained_pct']}% |"
        )
    a("")
    a("## Conclusion")
    a("")
    for para in payload["conclusion"]:
        a(para)
        a("")
    a("## References")
    a("")
    a(
        "- `docs/references/m8-xstocks-edge-quant.md` (M0 go/no-go)  \n"
        "- Phase-1 prior art: `src/mba/m6_attribution.py`, "
        "`docs/references/mm-attribution-analysis.md`  \n"
        "- Bot DESIGN §8 (sibling `mantle-stocks-arbitrage-bots`)"
    )
    a("")
    a(
        f"*Companion JSON: `docs/references/m8-onchain-fill-validation.json` "
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

    ctx = load_market_context("bybit-fluxion", load_collector=False, repo_root=_REPO)
    assert ctx.pairs is not None
    metrics_cfg = ctx.metrics
    pairs_amm = ctx.pairs.pairs_with_amm()
    quote_decimals = ctx.dex.quote_decimals

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    gaps = _eq.load_gaps(conn)
    book_span = _eq.table_span(conn, "bybit_book", "recv_ts_ms")
    if book_span.min_ms is None or book_span.max_ms is None:
        print("bybit_book is empty — cannot run study", file=sys.stderr)
        return 2

    since_ms = book_span.min_ms
    until_ms = book_span.max_ms
    span_ms = max(1, until_ms - since_ms)
    calendar_days = Decimal(span_ms) / Decimal(86_400_000)

    sample_ms = int(args.sample_ms)
    align_ms = int(args.align_ms)
    max_gap = int(args.max_gap_ms)
    trade_dur = int(args.trade_duration_ms)
    reentry = int(args.reentry_cooldown_ms)
    top_n = int(args.top_n)
    default_gate = metrics_cfg.max_abs_amm_spread_bps

    print(
        f"study window {_ms_iso(since_ms)} → {_ms_iso(until_ms)} "
        f"({span_ms / 3.6e6:.1f}h), sample={sample_ms}ms, top_n={top_n}",
        flush=True,
    )

    # pair_id → pools list (for window-open as-of)
    pools_by_pair: dict[str, list[FluxionPoolStateTick]] = {}
    pairs_by_id = {p.id: p for p in pairs_amm}
    # (pair, dir, session) → metas
    metas_by_key: dict[tuple[str, str, str], list[SampleMeta]] = defaultdict(list)
    # (pair, dir, session) → samples at default gate
    samples_by_key: dict[tuple[str, str, str], list[EdgeSample]] = defaultdict(list)
    # for anomaly sensitivity: gate → all samples
    samples_by_gate: dict[Decimal, list[EdgeSample]] = {
        g: [] for g in ANOMALY_GATES_BPS
    }
    # cache books/depths per pair so we only load once
    all_windows: list[OpportunityWindow] = []
    swaps_by_pair: dict[str, list[DecodedSwapView]] = {}
    n_swaps_total = 0

    for pair in pairs_amm:
        print(f"  loading {pair.id}…", flush=True)
        books = _eq.load_bucketed_books(
            conn, pair.id, sample_ms=sample_ms, since_ms=since_ms, until_ms=until_ms
        )
        pools = _eq.load_pools(
            conn, pair.id, since_ms=since_ms - align_ms, until_ms=until_ms
        )
        depths = _eq.load_depths(
            conn, pair.id, since_ms=since_ms - align_ms, until_ms=until_ms
        )
        pools_by_pair[pair.id] = pools
        print(
            f"    books={len(books)} pools={len(pools)} depth={len(depths)}",
            flush=True,
        )

        # Default-gate samples + meta (for top-N classification).
        samples, metas = build_amm_samples_with_meta(
            pair=pair,
            books=books,
            pools=pools,
            depths=depths,
            metrics_cfg=metrics_cfg,
            quote_decimals=quote_decimals,
            size_usd=SIZE_USD,
            gaps=gaps,
            align_ms=align_ms,
            max_abs_spread_bps=default_gate,
        )
        print(f"    AMM $1000 @ gate {default_gate}: {len(samples)} samples", flush=True)
        for s, meta in zip(samples, metas, strict=True):
            key = _meta_key(s.pair_id, s.direction, s.session)
            samples_by_key[key].append(s)
            metas_by_key[key].append(meta)
        # Also stash under the gate that matches default (usually 500).
        if default_gate in samples_by_gate:
            samples_by_gate[default_gate].extend(samples)

        # Other anomaly gates (skip recompute when equal to default).
        for gate in ANOMALY_GATES_BPS:
            if gate == default_gate:
                continue
            g_samples, _ = build_amm_samples_with_meta(
                pair=pair,
                books=books,
                pools=pools,
                depths=depths,
                metrics_cfg=metrics_cfg,
                quote_decimals=quote_decimals,
                size_usd=SIZE_USD,
                gaps=gaps,
                align_ms=align_ms,
                max_abs_spread_bps=gate,
            )
            samples_by_gate[gate].extend(g_samples)
            print(f"    AMM $1000 @ gate {gate}: {len(g_samples)} samples", flush=True)

        # Token order for notional / fill price: first pool tick if any.
        if not pools:
            print("    swaps=0 (no pool state → skip token-order)", flush=True)
            swaps_by_pair[pair.id] = []
            continue
        t0_is_q = quote_is_token0_for_pair(pair, pools[0])
        if t0_is_q is None:
            print("    swaps=0 (cannot resolve token0 quote order)", flush=True)
            swaps_by_pair[pair.id] = []
            continue
        swaps = load_swaps(
            conn,
            pair.id,
            since_ms=since_ms,
            until_ms=until_ms,
            token0_is_quote=t0_is_q,
        )
        swaps_by_pair[pair.id] = swaps
        n_swaps_total += len(swaps)
        print(f"    swaps={len(swaps)}", flush=True)

    # Detect windows per series at T=0.
    for _key, samples in samples_by_key.items():
        samples_sorted = sorted(samples, key=lambda s: s.ts_ms)
        wins = detect_windows(
            samples_sorted, min_edge_bps=Decimal(0), max_gap_ms=max_gap
        )
        all_windows.extend(wins)

    # Portfolio headline at default gate.
    port_profit = portfolio_capturable_profit(
        all_windows,
        inventory_usd=INVENTORY_USD,
        max_trade_usd=MAX_TRADE_USD,
        trade_duration_ms=trade_dur,
        reentry_cooldown_ms=reentry,
    )
    profit_per_day = port_profit / calendar_days if calendar_days > 0 else Decimal(0)

    # Rank top-N by trade PnL.
    ranked = sorted(all_windows, key=lambda w: w.trade_pnl_usd, reverse=True)
    top = ranked[:top_n]
    top_profit = sum((w.trade_pnl_usd for w in top), Decimal(0))
    top_share = (
        (top_profit / port_profit * Decimal(100)) if port_profit > 0 else Decimal(0)
    )

    top_rows: list[dict[str, Any]] = []
    class_profit: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    class_n: dict[str, int] = defaultdict(int)
    taken_profit = Decimal(0)

    for w in top:
        pair = pairs_by_id[w.pair_id]
        pools = pools_by_pair[w.pair_id]
        w_metas = _window_metas(metas_by_key, w)
        # Prefer the first in-window sample's pool (already align-gated); fall
        # back to as-of at window open with the same align cap used for samples.
        if w_metas:
            amm = w_metas[0].pool
            open_tick = w_metas[0].pool_tick
            open_age = w_metas[0].pool_age_ms
        else:
            amm, open_tick, open_age = _pool_as_of(
                pools,
                pair,
                ts_ms=w.start_ms,
                quote_decimals=quote_decimals,
                max_age_ms=align_ms,
            )
        _, end_tick, _ = _pool_as_of(
            pools,
            pair,
            ts_ms=w.end_ms,
            quote_decimals=quote_decimals,
            max_age_ms=None,  # block boundary only
        )
        # As-of pool-tick blocks at window edges (may lag open by ≤ align_ms).
        start_block: int | None = (
            open_tick.block_number if open_tick is not None else None
        )
        end_block: int | None = end_tick.block_number if end_tick is not None else None

        # Match swaps strictly on recv_ts inside the paper window. Block columns
        # are diagnostic (as-of pool ticks), not an expanded match range — using
        # them as a union can pull pre-window swaps into `taken`.
        in_swaps = swaps_in_window(
            swaps_by_pair.get(w.pair_id, []),
            start_ms=w.start_ms,
            end_ms=w.end_ms,
        )

        match = classify_window(
            paper_direction=w.direction,
            size_usd=SIZE_USD,
            swaps_in_range=in_swaps,
            pool=amm,
        )
        ages = [m.pool_age_ms for m in w_metas]
        if open_age is not None and not ages:
            ages = [open_age]
        age_stats = pool_age_stats(ages)
        bases = [m.abs_basis_bps for m in w_metas if m.abs_basis_bps is not None]
        max_basis = max(bases) if bases else None
        vq = match.virtual_quote_usd

        class_n[match.classification] += 1
        class_profit[match.classification] += w.trade_pnl_usd
        if match.classification == "taken":
            taken_profit += w.trade_pnl_usd

        dir_short = (
            "buy_fluxion_sell"
            if w.direction.startswith("buy_fluxion")
            else "buy_bybit_sell"
        )
        matching_swaps_out = [_swap_row_dict(sw) for sw in match.matching_swaps]
        all_swaps_out = [_swap_row_dict(sw) for sw in in_swaps]

        top_rows.append(
            {
                "pair_id": w.pair_id,
                "direction": w.direction,
                "direction_short": dir_short,
                "session": w.session,
                "start": _ms_iso(w.start_ms),
                "end": _ms_iso(w.end_ms),
                "start_ms": w.start_ms,
                "end_ms": w.end_ms,
                "start_block": start_block if start_block is not None else "—",
                "end_block": end_block if end_block is not None else "—",
                "start_block_n": start_block,
                "end_block_n": end_block,
                "duration_s": round(w.duration_ms / 1000, 1),
                "n_samples": w.n_samples,
                "peak_edge_bps": _fmt(w.peak_edge_bps, 2),
                "trade_pnl_usd": _fmt(w.trade_pnl_usd),
                "classification": match.classification,
                "profitable_swap_direction": match.profitable_swap_direction,
                "n_swaps_total": match.n_swaps_total,
                "n_swaps_matching": match.n_swaps_matching,
                "matching_swaps": matching_swaps_out,
                "all_swaps": all_swaps_out,
                "depth_adequate": match.depth_adequate,
                "virtual_quote_usd": _fmt(vq) if vq is not None else "—",
                "pool_age": age_stats,
                "pool_age_median_ms": age_stats["median_ms"],
                "max_abs_basis_bps": _fmt(max_basis, 1) if max_basis is not None else "—",
            }
        )

    # Classification summary rows.
    class_counts = []
    for klass in ("taken", "untaken_with_liquidity", "untaken_too_thin"):
        p = class_profit[klass]
        share = (p / top_profit * Decimal(100)) if top_profit > 0 else Decimal(0)
        class_counts.append(
            {
                "classification": klass,
                "n": class_n[klass],
                "profit_usd": _fmt(p),
                "share_pct": _fmt(share, 1),
            }
        )

    validated_port = (
        (taken_profit / port_profit * Decimal(100)) if port_profit > 0 else Decimal(0)
    )
    validated_top = (
        (taken_profit / top_profit * Decimal(100)) if top_profit > 0 else Decimal(0)
    )

    # Anomaly sensitivity (retained_pct filled in a second pass vs 500-bps baseline).
    anomaly_rows: list[dict[str, Any]] = []
    for gate in ANOMALY_GATES_BPS:
        g_samples = samples_by_gate[gate]
        # Group into series and detect windows.
        by_series: dict[tuple[str, str, str], list[EdgeSample]] = defaultdict(list)
        for s in g_samples:
            by_series[(s.pair_id, s.direction, s.session)].append(s)
        g_wins: list[OpportunityWindow] = []
        for samples in by_series.values():
            g_wins.extend(
                detect_windows(
                    sorted(samples, key=lambda x: x.ts_ms),
                    min_edge_bps=Decimal(0),
                    max_gap_ms=max_gap,
                )
            )
        g_profit = portfolio_capturable_profit(
            g_wins,
            inventory_usd=INVENTORY_USD,
            max_trade_usd=MAX_TRADE_USD,
            trade_duration_ms=trade_dur,
            reentry_cooldown_ms=reentry,
        )
        anomaly_rows.append(
            {
                "gate_bps": str(int(gate)),
                "n_samples": len(g_samples),
                "n_windows": len(g_wins),
                "profit_total_usd": _fmt(g_profit),
                "profit_per_day_usd": _fmt(
                    g_profit / calendar_days if calendar_days > 0 else Decimal(0)
                ),
                "profit_total_dec": g_profit,
            }
        )

    # Second pass for retained_pct against the 500-bps baseline row
    # (M0 / config default gate; column label is "retained vs 500").
    base = next(
        (Decimal(r["profit_total_dec"]) for r in anomaly_rows if r["gate_bps"] == "500"),
        Decimal(0),
    )
    for r in anomaly_rows:
        p = Decimal(r.pop("profit_total_dec"))
        if base > 0:
            retained_dec = p / base * Decimal(100)
        elif p == 0:
            retained_dec = Decimal(100)
        else:
            retained_dec = Decimal(0)
        r["retained_pct"] = _fmt(retained_dec, 1)
        r["retained_dec"] = retained_dec

    # Conclusion paragraphs.
    n_taken = class_n["taken"]
    n_liq = class_n["untaken_with_liquidity"]
    n_thin = class_n["untaken_too_thin"]
    retained_100 = next(
        (r["retained_pct"] for r in anomaly_rows if r["gate_bps"] == "100"), "—"
    )
    retained_200 = next(
        (r["retained_pct"] for r in anomaly_rows if r["gate_bps"] == "200"), "—"
    )
    retained_300 = next(
        (r["retained_pct"] for r in anomaly_rows if r["gate_bps"] == "300"), "—"
    )
    retained_200_dec = next(
        (Decimal(r["retained_dec"]) for r in anomaly_rows if r["gate_bps"] == "200"),
        Decimal(0),
    )

    # Staleness dig for class (b).
    class_b = [
        w for w in top_rows if w["classification"] == "untaken_with_liquidity"
    ]
    if class_b:
        med_ages = [int(w["pool_age"]["median_ms"]) for w in class_b]
        max_ages = [int(w["pool_age"]["max_ms"]) for w in class_b]
        near_gate = sum(1 for a in max_ages if a >= align_ms * 8 // 10)
        staleness_note = (
            f"Of {len(class_b)} untaken-with-liquidity top windows, median pool-join "
            f"ages (conditional on passing the {align_ms} ms align gate used at "
            f"sample construction) range {min(med_ages)}–{max(med_ages)} ms "
            f"(max ages up to {max(max_ages)} ms). "
        )
        if near_gate:
            staleness_note += (
                f"{near_gate} of those windows have max age ≥80% of the align gate — "
                "treat them as residual join-staleness candidates even among the "
                "surviving samples."
            )
        else:
            staleness_note += (
                "Surviving samples sit well inside the align gate, so the untaken "
                "status is more consistent with a real but uncontested (or "
                "risk-blocked) dislocation than with a stale pool snapshot among "
                "the samples that passed the join filter. This dig cannot speak "
                "to joins older than the gate — those never entered a window."
            )
    else:
        staleness_note = (
            "No top-N window fell in untaken-with-liquidity, so the as-of join "
            "staleness dig did not fire on the profit-dominant set."
        )

    # Interpret anomaly sensitivity (commit to a reading, not a template).
    if retained_200_dec == 0:
        anomaly_reading = (
            f"Tightening `pricing_anomaly` from 500 → 300 / 200 / 100 bps retains "
            f"**{retained_300}% / {retained_200}% / {retained_100}%** of the "
            f"500-bps portfolio profit. **At 200 bps the entire portfolio "
            f"headline disappears** (0 windows) — every profitable window in this "
            f"span has |AMM−CEX| basis large enough that a modestly tighter gate "
            f"rejects it. The go-case therefore rests on quotes the tooling itself "
            f"nearly flags as `pricing_anomaly` (default gate 500 bps)."
        )
    else:
        anomaly_reading = (
            f"Tightening `pricing_anomaly` from 500 → 300 / 200 / 100 bps retains "
            f"**{retained_300}% / {retained_200}% / {retained_100}%** of the "
            f"500-bps portfolio profit. The retained share at 200 bps is the "
            f"robust core; the drop from 500 is the portion sitting near the "
            f"anomaly gate."
        )

    # Taken-fill notionals vs study size (do not overclaim $1k validation).
    taken_notionals: list[Decimal] = []
    for wrow in top_rows:
        if wrow["classification"] != "taken":
            continue
        for sw in wrow["matching_swaps"]:
            raw = sw["notional_usd"]
            if raw in (None, "—"):
                continue
            taken_notionals.append(Decimal(str(raw)))
    if taken_notionals:
        max_taken_n = max(taken_notionals)
        taken_n_str = ", ".join(_fmt(n) for n in sorted(taken_notionals, reverse=True))
        size_caveat = (
            f"Matching on-chain fills in the top {top_n} were notionals "
            f"**{taken_n_str} USDT** (max {_fmt(max_taken_n)} vs study size "
            f"{SIZE_USD}). **No observed fill reached the $1,000 study notional** — "
            f"`taken` validates direction and firmness at the size that actually "
            f"traded, not full study-size depth."
        )
    else:
        size_caveat = (
            f"No matching on-chain fills in the top {top_n}, so there is no "
            f"observed fill size to compare against the ${SIZE_USD} study notional."
        )

    thin_note = (
        f"Class (c) count is **{n_thin}** — under this pipeline that class is "
        f"empty by construction whenever a window has an align-gated pool "
        f"snapshot (samples already required $1,000 fillability). Do not read "
        f"the zero as independent depth evidence."
    )

    conclusion = [
        (
            f"Over the available raw journal span ({_ms_iso(since_ms)} → "
            f"{_ms_iso(until_ms)}), portfolio single-flight capturable profit at "
            f"AMM $1,000 / T=0 is **{_fmt(port_profit)} USDT** "
            f"({_fmt(profit_per_day)} USDT/day), across **{len(all_windows)}** "
            f"windows. The top {top_n} windows' raw trade-PnL sum is "
            f"**{_fmt(top_share, 1)}%** of that portfolio total (can exceed 100% "
            f"when high-PnL windows overlap; portfolio is single-flight)."
        ),
        (
            f"Classification of the top {top_n}: **{n_taken} taken**, "
            f"**{n_liq} untaken-with-liquidity**, **{n_thin} untaken-too-thin**. "
            f"On-chain matching swaps validate **{_fmt(validated_top, 1)}%** of "
            f"top-{top_n} raw trade-PnL and **{_fmt(validated_port, 1)}%** of the "
            f"portfolio single-flight headline (numerator is raw per-window "
            f"`trade_pnl_usd`, same caveat as the top-N share footnote). "
            f"Journal swap activity in-span is sparse ({n_swaps_total} swaps "
            f"total) — most large paper windows had **nobody** trading the pool "
            f"in the profitable direction while the edge was open. {size_caveat} "
            f"{thin_note}"
        ),
        staleness_note,
        anomaly_reading,
        (
            "**Implication for the bot go-case:** only the **taken** share is hard "
            "evidence that large dislocations were real and firm enough for "
            "someone to trade — and even then only at the fill size observed, "
            "not necessarily at $1,000. Untaken-with-liquidity windows need a "
            "human explanation (MM risk limits, gas, inventory, residual join "
            "risk) before sizing — and given the anomaly-gate sensitivity, any "
            "live size-up must also survive a tighter basis scrub. Class (c) is "
            "not informative under the current paper path (see method note). "
            "Re-run after ≥5 clean RTH sessions so the original M0 concentration "
            "claim (two HOODx windows ≈ 98% of HOODx profit) can be re-checked "
            "on a longer raw span — raw `bybit_book` / `bybit_depth` retention "
            "is ~2 days, so the original 2026-08-03→05 M0 study ticks are mostly "
            "pruned."
        ),
    ]

    # Drop internal Decimal retained fields from JSON payload.
    for r in anomaly_rows:
        r.pop("retained_dec", None)

    data_span_note = (
        f"M0 (`m8-xstocks-edge-quant.md`) used raw ticks spanning "
        f"**2026-08-03 14:51 UTC → 2026-08-05 14:48 UTC**. Collector retention "
        f"keeps raw `bybit_book` / `bybit_depth` for ~2 days, so that study "
        f"window is **no longer fully present** in the journal. This note "
        f"recomputes windows on the **currently retained** raw span "
        f"({_ms_iso(since_ms)} → {_ms_iso(until_ms)}) with the same methodology "
        f"(sample_ms={sample_ms}, align_ms={align_ms}, size=$1000, T=0, "
        f"pricing_anomaly gate={default_gate} bps). `fluxion_pool_state` and "
        f"`fluxion_swaps` still cover a longer history; swaps are joined on "
        f"`recv_ts_ms` inside each detected window. Numbers are therefore a "
        f"**method-matched re-run**, not a byte-for-byte replay of the M0 "
        f"window list — the scientific question (are large paper dislocations "
        f"taken on-chain?) is unchanged."
    )

    elapsed = time.time() - t0
    repro = (
        f"uv run python scripts/xstocks_fill_validation.py "
        f"--db data/monitor-bybit-fluxion.db --sample-ms {sample_ms}"
    )

    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_utc": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "repro_command": repro,
        "elapsed_s": round(elapsed, 1),
        "method": {
            "market": "bybit-fluxion",
            "engine": "monitor.metrics.pnl_v2.compute_pnl_usd",
            "windows": "monitor.analysis.edge_quant.detect_windows",
            "classification": "monitor.analysis.fill_validation",
            "size_usd": str(SIZE_USD),
            "sample_ms": sample_ms,
            "align_ms": align_ms,
            "max_gap_ms": max_gap,
            "trade_duration_ms": trade_dur,
            "reentry_cooldown_ms": reentry,
            "min_edge_bps": "0",
            "pricing_anomaly_gate_default": str(default_gate),
            "top_n": top_n,
            "inventory_usd": str(INVENTORY_USD),
            "max_trade_usd": str(MAX_TRADE_USD),
        },
        "data_span_note": data_span_note,
        "study": {
            "start": _ms_iso(since_ms),
            "end": _ms_iso(until_ms),
            "span_hours": span_ms / 3_600_000,
            "calendar_days": float(calendar_days),
            "n_swaps": n_swaps_total,
            "n_swap_pairs": sum(1 for s in swaps_by_pair.values() if s),
            "n_amm_pairs": len(pairs_amm),
        },
        "headline": {
            "profit_total_usd": _fmt(port_profit),
            "profit_per_day_usd": _fmt(profit_per_day),
            "n_windows": len(all_windows),
            "top_n": top_n,
            "top_n_profit_usd": _fmt(top_profit),
            "top_n_profit_share_pct": _fmt(top_share, 1),
            "taken_profit_usd": _fmt(taken_profit),
            "validated_share_of_portfolio_pct": _fmt(validated_port, 1),
            "validated_share_of_top_n_pct": _fmt(validated_top, 1),
            "class_counts": class_counts,
        },
        "top_windows": top_rows,
        "anomaly_sensitivity": anomaly_rows,
        "conclusion": conclusion,
    }

    report_path = Path(args.report)
    json_path = Path(args.json_out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(payload), encoding="utf-8")
    json_path.write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(f"\nWrote {report_path}")
    print(f"Wrote {json_path}")
    print(
        f"Portfolio {_fmt(port_profit)} USDT; top-{top_n} taken share "
        f"{_fmt(validated_top, 1)}%; elapsed {elapsed:.1f}s"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        default=str(_REPO / "data" / "monitor-bybit-fluxion.db"),
        help="Path to bybit-fluxion journal SQLite",
    )
    p.add_argument("--report", default=str(DEFAULT_REPORT))
    p.add_argument("--json-out", default=str(DEFAULT_JSON))
    p.add_argument("--sample-ms", type=int, default=DEFAULT_SAMPLE_MS)
    p.add_argument("--align-ms", type=int, default=DEFAULT_ALIGN_MS)
    p.add_argument("--max-gap-ms", type=int, default=DEFAULT_MAX_GAP_MS)
    p.add_argument("--trade-duration-ms", type=int, default=DEFAULT_TRADE_DURATION_MS)
    p.add_argument(
        "--reentry-cooldown-ms", type=int, default=DEFAULT_REENTRY_COOLDOWN_MS
    )
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
