"""M5 -- profitable-window duration, tail stats, and the report.

Duration is the first-class metric. "A spread existed" is nearly meaningless on
its own: a 3-second window is noise no bot can act on after block inclusion,
while a 20-minute window is a standing invitation. So the primary output is the
DISTRIBUTION of how long each profitable window stayed open, time-weighted.

How the merged timeline works
-----------------------------
The DEX side is a step function -- pool state genuinely does not change between
the blocks M1 found, so a long interval is EXACT, not stale. The Bybit side
moves continuously. So we evaluate at every Bybit print, ASOF-joining it onto
the DEX interval that contains it, and compare its mid against that interval's
breakeven mid (see M4). A profitable window is a maximal run of consecutive
prints on the profitable side; it closes at the first print that is not.

Resolution floor, and it is worse than it looks: we only see Bybit at print
times, and prints are bursty. The median gap is 4ms, but weighted by the time it
actually covers the mean gap is 12s -- quiet stretches dominate the clock. So the
effective resolution is COARSER than the 4s actionable floor, not finer, and a
duration near that floor carries granularity error of the same order in both
directions. `_resolution()` computes all three figures and the report states
them; treat the >= 4s split as a bucket, not a measurement.

Why uncaptured profit is not a sum over observations
---------------------------------------------------
The same pool liquidity can only be taken once; after one fill the price has
moved. Summing net profit across every profitable observation would multiply one
opportunity by the number of times we looked at it. Instead each WINDOW
contributes one round trip, valued at the profit available when it opened (what
a bot reacting immediately would capture). The in-window peak is reported
alongside it as an upper bound.

The did-anyone-take-it check
----------------------------
For each profitable window we ask whether any on-chain swap actually landed
inside it. That converts the headline question from an inference into a count.
"""

from __future__ import annotations

import argparse

import duckdb
import polars as pl

from .config import (BYBIT_TAKER_FEE, DATA, INVENTORY_APR,
                     INVENTORY_CAPITAL_MULT, REPORT, SIZE_LADDER_USD)
from .m1_scan_events import RAW_LOGS
from .m3_bybit import QUOTES as BYBIT_QUOTES
from .m4_align import INTERVALS, OPPS, round_trip_hurdle_bps, slip_table

WINDOWS = DATA / "windows.parquet"
SUMMARY = DATA / "window_summary.parquet"
REPORT_MD = REPORT / "report.md"

# Actionability floor. To take a window you must see the Bybit print, submit,
# and get included -- on Mantle's 2s blocks you cannot count on the very next
# block, so two blocks is the optimistic floor for a colocated bot. Windows
# below this are arithmetic, not opportunity, and the report says so rather
# than letting a large window COUNT imply a large opportunity.
ACTIONABLE_MS = 4_000


def _swap_ts(con: duckdb.DuckDBPyConnection) -> bool:
    """Real on-chain swaps, for the did-anyone-take-it check.

    Returns True if the swaps carry a direction. A direction-A window means "buy
    MNT on the DEX", and only a swap that actually bought MNT there took it -- a
    sell landing in the same window is unrelated flow. M6 decodes direction, so
    prefer its output; fall back to the direction-blind version if M6 has not run
    yet (it runs after M5 in the documented order), and say so in the report
    rather than quietly overstating how many windows were taken.
    """
    decoded = DATA / "swaps_decoded.parquet"
    if decoded.exists():
        con.execute(f"""
            CREATE OR REPLACE TEMP VIEW swaps AS
            SELECT DISTINCT pool_name, direction, ts_ms
            FROM read_parquet('{decoded}')
        """)
        return True
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW swaps AS
        SELECT DISTINCT r.pool_name, NULL AS direction, s.ts_ms
        FROM read_parquet('{RAW_LOGS}') r
        JOIN read_parquet('{DATA / "state_changes.parquet"}') s
          ON s.pool_name = r.pool_name AND s.block_number = r.block_number
        WHERE r.event_name = 'Swap'
    """)
    print("  !! swaps_decoded.parquet missing -- the did-anyone-take-it check "
          "will count swaps in EITHER direction (run m6_attribution first for "
          "the direction-aware version)")
    return False


def assign_prints(con: duckdb.DuckDBPyConnection, iv: pl.DataFrame,
                  pool: str) -> int:
    """Assign every Bybit print to the DEX interval containing it, once per pool.

    The interval boundaries are the pool's state-change blocks, so they are
    IDENTICAL across all 12 (direction x size) combinations -- verified: every
    combination carries the pool's full block set. Doing the assignment once per
    pool instead of once per combination is 2 passes rather than 24.

    Done in Polars, not with DuckDB's ASOF JOIN: with no equality predicate to
    partition on, DuckDB range-scans and this join does not finish in reasonable
    time (see the note in m4_align). join_asof is a single merge pass.

    Returns the pool's total observed span in ms, which is likewise identical
    across combinations and is the denominator for "fraction of time profitable".
    """
    blocks = (iv.filter(pl.col("pool_name") == pool)
                .select("block_number", "ts_ms").unique().sort("ts_ms"))
    lo, hi = blocks["ts_ms"].min(), blocks["ts_ms"].max()
    # Bound to the pool's own observed span: a print outside it has no interval
    # to belong to, and assigning one to the last interval would stretch the
    # measured window past the data.
    byb = (pl.read_parquet(BYBIT_QUOTES)
             .filter(pl.col("timestamp").is_between(lo, hi))
             .select(pl.col("timestamp").alias("ts"), "mid", "crossed")
             .sort("ts"))
    asg = byb.join_asof(blocks, left_on="ts", right_on="ts_ms",
                        strategy="backward").drop("ts_ms")
    con.register("asg_arrow", asg.to_arrow())
    con.execute("CREATE OR REPLACE TEMP TABLE asg AS SELECT * FROM asg_arrow")
    con.unregister("asg_arrow")
    # Span measured the same way a window is: each print is held until the next.
    return int(asg["ts"].max() - asg["ts"].min())


def find_windows(con: duckdb.DuckDBPyConnection, iv: pl.DataFrame,
                 pool: str, direction: str, size: int,
                 span_ms: int) -> pl.DataFrame:
    """Maximal runs of profitability for one (pool, direction, size).

    Requires assign_prints() to have been run for this pool; `span_ms` is what
    it returned.
    """
    sub = iv.filter((pl.col("pool_name") == pool)
                    & (pl.col("direction") == direction)
                    & (pl.col("size_usd") == size)).sort("ts_ms")
    if sub.is_empty():
        return pl.DataFrame()

    con.register("iv_sub", sub.to_arrow())
    # Direction A profits when Bybit is ABOVE breakeven (we sell there);
    # direction B when it is BELOW (we buy there).
    cmp_op = ">" if direction == "A" else "<"

    q = f"""
    WITH j AS (
        SELECT a.ts, a.mid, a.crossed,
               i.breakeven_mid, i.qty_mnt, i.usd_leg, i.gas_usd,
               i.sell_slip, i.buy_slip, i.block_number
        FROM asg a
        JOIN iv_sub i USING (block_number)
    ),
    f AS (
        SELECT *,
               mid {cmp_op} breakeven_mid AS prof,
               LEAD(ts) OVER (ORDER BY ts) AS next_ts,
               CASE WHEN '{direction}' = 'A'
                    THEN qty_mnt * mid * (1 - sell_slip) * (1 - {BYBIT_TAKER_FEE})
                         - usd_leg - gas_usd
                    ELSE usd_leg - qty_mnt * mid * (1 + buy_slip)
                         * (1 + {BYBIT_TAKER_FEE}) - gas_usd
               END AS profit_usd
        FROM j
    ),
    g AS (
        SELECT *, SUM(chg) OVER (ORDER BY ts ROWS UNBOUNDED PRECEDING) AS grp
        FROM (
            SELECT *, CASE WHEN prof IS DISTINCT FROM
                        LAG(prof) OVER (ORDER BY ts) THEN 1 ELSE 0 END AS chg
            FROM f
        )
    )
    SELECT grp,
           min(ts)                        AS open_ts,
           max(COALESCE(next_ts, ts))     AS close_ts,
           max(COALESCE(next_ts, ts)) - min(ts) AS duration_ms,
           count(*)                       AS n_obs,
           first(profit_usd ORDER BY ts)  AS profit_at_open_usd,
           max(profit_usd)                AS profit_peak_usd,
           max(mid)                       AS mid_max,
           min(mid)                       AS mid_min,
           any_value(breakeven_mid)       AS breakeven_mid_sample,
           count(DISTINCT block_number)   AS n_dex_intervals,
           sum(CASE WHEN crossed THEN 1 ELSE 0 END) AS n_crossed
    FROM g
    WHERE prof
    GROUP BY grp
    ORDER BY open_ts
    """
    w = con.execute(q).pl()
    con.unregister("iv_sub")
    if w.is_empty():
        return pl.DataFrame()

    return w.with_columns(
        pl.lit(pool).alias("pool_name"),
        pl.lit(direction).alias("direction"),
        pl.lit(size).alias("size_usd"),
        pl.lit(span_ms).alias("observed_span_ms"),
    )


def took_it(con: duckdb.DuckDBPyConnection, w: pl.DataFrame,
            direction_aware: bool) -> pl.DataFrame:
    """Did a real on-chain swap, in this window's own direction, land inside it?"""
    if w.is_empty():
        return w
    con.register("w", w.to_arrow())
    dir_pred = "AND s.direction = w.direction" if direction_aware else ""
    out = con.execute(f"""
        SELECT w.*, COALESCE(s.n_swaps, 0) AS n_swaps_in_window
        FROM w
        LEFT JOIN (
            SELECT w.grp, w.pool_name, w.direction, w.size_usd,
                   count(*) AS n_swaps
            FROM w JOIN swaps s
              ON s.pool_name = w.pool_name
             AND s.ts_ms >= w.open_ts AND s.ts_ms < w.close_ts
             {dir_pred}
            GROUP BY 1,2,3,4
        ) s USING (grp, pool_name, direction, size_usd)
    """).pl()
    con.unregister("w")
    return out


def run() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    iv = pl.read_parquet(INTERVALS)
    con = duckdb.connect()
    con.execute(f"CREATE VIEW byb AS SELECT * FROM read_parquet('{BYBIT_QUOTES}')")
    dir_aware = _swap_ts(con)

    parts = []
    for pool in sorted(iv["pool_name"].unique()):
        span_ms = assign_prints(con, iv, pool)
        for direction in ["A", "B"]:
            for size in SIZE_LADDER_USD:
                w = find_windows(con, iv, pool, direction, size, span_ms)
                if not w.is_empty():
                    parts.append(took_it(con, w, dir_aware))
                print(f"  {pool:8} dir {direction} ${size:>6,}: "
                      f"{0 if w.is_empty() else len(w):>5} windows")

    if not parts:
        windows = pl.DataFrame()
        print("\nNO profitable window at any size, in either direction, on "
              "either venue, over the entire 29-day window.")
    else:
        windows = pl.concat(parts, how="diagonal")
        windows.write_parquet(WINDOWS)

    summary = _summarise(windows, iv)
    summary.write_parquet(SUMMARY)
    _write_md(windows, summary, iv, dir_aware)
    try:
        from .charts import make_charts
        make_charts(windows, summary, iv)
    except Exception as exc:  # noqa: BLE001 - charts must never block the numbers
        print(f"  !! charts skipped: {exc}")
    print(f"\nwrote {REPORT_MD}")


def _summarise(windows: pl.DataFrame, iv: pl.DataFrame) -> pl.DataFrame:
    if windows.is_empty():
        return pl.DataFrame({
            "pool_name": [], "direction": [], "size_usd": [], "n_windows": [],
        })
    s = windows.group_by("pool_name", "direction", "size_usd").agg(
        pl.len().alias("n_windows"),
        pl.col("duration_ms").sum().alias("total_ms"),
        pl.col("observed_span_ms").max().alias("span_ms"),
        pl.col("duration_ms").median().alias("dur_median_ms"),
        pl.col("duration_ms").quantile(0.95).alias("dur_p95_ms"),
        pl.col("duration_ms").quantile(0.99).alias("dur_p99_ms"),
        pl.col("duration_ms").max().alias("dur_max_ms"),
        pl.col("profit_at_open_usd").sum().alias("uncaptured_usd"),
        pl.col("profit_peak_usd").sum().alias("uncaptured_peak_usd"),
        pl.col("profit_peak_usd").max().alias("best_single_usd"),
        pl.col("n_swaps_in_window").sum().alias("swaps_inside"),
        (pl.col("n_swaps_in_window") > 0).sum().alias("windows_with_a_swap"),
        # Restricted to windows a bot could actually have reached (see
        # ACTIONABLE_MS). These, not the raw counts, are the decision numbers.
        (pl.col("duration_ms") >= ACTIONABLE_MS).sum().alias("n_actionable"),
        pl.col("duration_ms").filter(pl.col("duration_ms") >= ACTIONABLE_MS)
          .sum().alias("actionable_ms"),
        pl.col("profit_at_open_usd")
          .filter(pl.col("duration_ms") >= ACTIONABLE_MS)
          .sum().alias("uncaptured_actionable_usd"),
        # Competition proxy: an actionable window that already contains someone
        # else's swap is one you would have had to win a race for.
        ((pl.col("duration_ms") >= ACTIONABLE_MS)
         & (pl.col("n_swaps_in_window") > 0)).sum().alias("n_actionable_contested"),
    ).with_columns(
        (pl.col("total_ms") / pl.col("span_ms") * 100).alias("pct_time_profitable"),
        (pl.col("actionable_ms") / pl.col("span_ms") * 100)
          .alias("pct_time_actionable"),
        (pl.col("dur_median_ms") / 1000).alias("dur_median_s"),
        (pl.col("dur_p95_ms") / 1000).alias("dur_p95_s"),
        (pl.col("dur_p99_ms") / 1000).alias("dur_p99_s"),
        (pl.col("dur_max_ms") / 1000).alias("dur_max_s"),
    ).sort(["pool_name", "direction", "size_usd"])
    return s


def _fmt_dur(ms: float | None) -> str:
    if ms is None:
        return "-"
    s = ms / 1000
    if s < 60:
        return f"{s:.1f}s"
    if s < 3600:
        return f"{s / 60:.1f}m"
    return f"{s / 3600:.2f}h"


def _carry_table(summary: pl.DataFrame, span_days: float) -> pl.DataFrame:
    """Actionable profit vs inventory carry, per ladder size.

    Single source of truth: the bottom-line verdict and the inventory table both
    read this, so they cannot disagree about which sizes clear their carry.
    """
    if summary.is_empty():
        return pl.DataFrame()
    t = summary.group_by("size_usd").agg(
        pl.col("uncaptured_actionable_usd").sum().alias("u")
    ).sort("size_usd")
    return t.with_columns(
        (pl.col("size_usd") * INVENTORY_CAPITAL_MULT).alias("capital"),
    ).with_columns(
        (pl.col("capital") * INVENTORY_APR * span_days / 365).alias("carry"),
    ).with_columns(
        pl.when(pl.col("carry") > 0)
          .then(pl.col("u") / pl.col("carry")).otherwise(0.0).alias("ratio"),
    )


def _rungs(sizes: list[int], all_sizes: list[int]) -> str:
    """Phrase a passing set of ladder rungs without implying rungs that do not
    exist -- "$1,000 and below" is vacuous when $1,000 is the smallest size run.
    """
    if not sizes:
        return "no rung"
    if len(sizes) == 1:
        return f"the ${sizes[0]:,} rung"
    if min(sizes) == min(all_sizes):
        return f"${max(sizes):,} and below"
    return f"${min(sizes):,}-${max(sizes):,}"


def _write_md(windows: pl.DataFrame, summary: pl.DataFrame,
              iv: pl.DataFrame, dir_aware: bool = True) -> None:
    opp = pl.read_parquet(OPPS)
    span_days = (iv["ts_ms"].max() - iv["ts_ms"].min()) / 86_400_000
    carry_t = _carry_table(summary, span_days)
    L: list[str] = []
    A = L.append

    A(f"# Mantle <> Bybit WMNT/USDT0 arbitrage: {span_days:.0f}-day backtest")
    A("")
    A(f"Window: {span_days:.1f} days ending "
      f"{pl.from_epoch(pl.Series([iv['ts_ms'].max()]), time_unit='ms')[0]:%Y-%m-%d %H:%M} UTC  ")
    A("Venues: Agni V3 (PancakeSwap-V3 fork) and Merchant Moe Liquidity Book "
      "v2.2, vs Bybit spot MNTUSDT  ")
    A(f"DEX quotes: {len(opp):,} contract-quoted points across "
      f"{iv['block_number'].n_unique():,} state-change blocks (exact step "
      f"function, not sampled)")
    A("")

    # The question that was actually asked, answered in one place. Everything
    # below is the evidence for it; a reader who stops here should not come away
    # with a different impression than one who reads to the end.
    A("## Bottom line")
    A("")
    if carry_t.is_empty():
        A("**No.** No profitable window existed at any size, so there is "
          "nothing to weigh against the cost of holding inventory.")
    else:
        ladder = carry_t["size_usd"].to_list()
        clears = carry_t.filter(pl.col("ratio") > 1)
        robust = carry_t.filter(pl.col("ratio") >= 2)
        best = carry_t.sort("ratio", descending=True).row(0, named=True)
        if clears.is_empty():
            A(f"**No.** Not one rung on the ladder earns its inventory carry. "
              f"The best case, ${best['size_usd']:,}, recovers "
              f"{best['ratio']:.2f}x of it.")
        else:
            top = int(clears["size_usd"].max())
            big = clears.sort("u", descending=True).row(0, named=True)
            A("**Not as a standalone business, on this evidence.** A real "
              "dislocation does open -- but only the small rungs clear their "
              "inventory carry, and they clear it by amounts too small to pay "
              "for the operation.")
            A("")
            A(f"- Profitable *only* up to **${top:,}** per trade. Above that, "
              f"execution cost outgrows the spread and every rung loses money "
              f"against carry.")
            A(f"- The best rung *relative to carry* is ${best['size_usd']:,}: "
              f"**${best['u']:,.0f} over {span_days:.0f} days** against "
              f"${best['carry']:,.2f} of carry, or {best['ratio']:.1f}x -- but "
              f"only ${best['u'] / span_days:,.2f} per day in absolute terms. "
              f"The largest absolute profit among rungs that clear carry at all "
              f"is ${big['u']:,.0f} at ${big['size_usd']:,} "
              f"(${big['u'] / span_days:,.2f}/day, {big['ratio']:.2f}x carry). "
              f"Neither covers a developer, a server, or a monitoring rota.")
            if not robust.is_empty():
                A(f"- Only {_rungs(robust['size_usd'].to_list(), ladder)} "
                  f"survives halving the profit to account for lost races, and "
                  f"{int(summary['n_actionable_contested'].sum()):,} of "
                  f"{int(summary['n_actionable'].sum()):,} actionable windows "
                  f"were already contested.")
        A("")
        A("The useful output of this study is therefore the *shape* of the "
          "opportunity -- where the size ceiling sits, which venue and "
          "direction carry it, and how briefly it lasts -- rather than a "
          "go-ahead. See **Inventory** below for the per-size arithmetic.")
    A("")

    A("## Headline")
    A("")
    if windows.is_empty():
        A("**No profitable arbitrage window existed at any size, in either "
          "direction, on either venue, at any point in the 29-day window.**")
        A("")
        A("The round-trip cost floor exceeds the observed price dislocation "
          "everywhere. See the cost decomposition below for why.")
    else:
        tot = summary.select(pl.col("n_windows").sum()).item()
        best = summary.sort("pct_time_profitable", descending=True).head(1)
        b = best.to_dicts()[0]
        act = int(summary["n_actionable"].sum())
        A(f"**{tot:,} profitable windows** were found across all "
          f"venue/direction/size combinations -- but only **{act:,} "
          f"({act / tot:.1%}) lasted long enough to be taken** "
          f"(>= {ACTIONABLE_MS / 1000:.0f}s; see below). Read the count with "
          f"that discount attached.")
        A("")
        A(f"The most persistent combination was **{b['pool_name']} direction "
          f"{b['direction']} at ${b['size_usd']:,}**: profitable "
          f"{b['pct_time_profitable']:.3f}% of elapsed time across "
          f"{b['n_windows']:,} windows, median duration "
          f"{_fmt_dur(b['dur_median_ms'])}, p99 {_fmt_dur(b['dur_p99_ms'])}, "
          f"longest {_fmt_dur(b['dur_max_ms'])}.")
        A("")
        A(f"Estimated uncaptured profit for that combination: "
          f"**${b['uncaptured_actionable_usd']:,.0f}** counting only windows a "
          f"bot could reach, one round trip each, valued at window open. "
          f"(${b['uncaptured_usd']:,.0f} if every flicker were also counted; "
          f"${b['uncaptured_peak_usd']:,.0f} if on top of that every window "
          f"were timed to its in-window peak -- both are upper bounds, not "
          f"forecasts.)")
        A("")
        swaps = int(b["windows_with_a_swap"])
        A(f"Of those {b['n_windows']:,} windows, **{swaps:,} "
          f"({swaps / b['n_windows']:.1%}) had at least one real on-chain swap "
          f"land inside them** -- the rest went untouched. "
          + ("Matched on direction as well as venue and time: a window that "
             "says 'buy MNT on the DEX' is only counted as taken by a swap that "
             "actually bought MNT there."
             if dir_aware else
             "**Direction-blind** -- counts a swap in either direction, so this "
             "figure is an upper bound. Run `m6_attribution` and re-run this "
             "stage for the direction-matched count."))

    A("")
    A("## Why the floor is where it is")
    A("")
    A("The hurdle is not the advertised 0.25% pool fee. Measured by quoting the "
      "real contracts:")
    A("")
    A("| venue | one-way cost vs mid @ $1K | @ $10K | @ $100K |")
    A("|---|---|---|---|")
    eff = _dex_cost_table()

    def _bps(v: float | None) -> str:
        return "-" if v is None else f"{v:.1f} bps"

    for row in eff.iter_rows(named=True):
        A(f"| {row['pool_name']} | {_bps(row['c1k'])} | "
          f"{_bps(row['c10k'])} | {_bps(row['c100k'])} |")
    A("")
    A(f"Add Bybit taker fee {BYBIT_TAKER_FEE * 1e4:.0f} bps, plus Bybit "
      f"slippage from mid (which already includes crossing the half-spread, so "
      f"it must not be added to it again). Bybit's own top-of-book is tight -- "
      f"median spread {_bybit_spread():.2f} bps -- but its depth is finite, and "
      f"that is what decides where the strategy stops scaling:")
    A("")
    A("| size | Bybit slippage, selling | buying | cheapest DEX leg | "
      "total round-trip hurdle* |")
    A("|---|---|---|---|---|")
    slips = slip_table()
    # Size-matched: the DEX leg gets more expensive with size too, so pairing
    # every rung with the $1K DEX cost would understate the large rungs badly.
    by_size = _dex_cost_by_size()
    cheapest = {r["size_usd"]: r["cost_bps"] for r in
                by_size.group_by("size_usd")
                       .agg(pl.col("cost_bps").min()).iter_rows(named=True)}
    for r in slips.iter_rows(named=True):
        s, b_, sz = r["sell_slip"], r["buy_slip"], r["size_usd"]
        dex = cheapest.get(sz)
        if s is None or b_ is None:
            A(f"| ${sz:,} | book cannot absorb | - | - | unfillable |")
            continue
        if dex is None:
            A(f"| ${sz:,} | {s * 1e4:.2f} bps | {b_ * 1e4:.2f} bps | "
              f"not quoted | - |")
            continue
        # Total from the shared helper, not recomputed here: M6 quotes the $1K
        # figure when sizing what venue choice saves, and two copies of this
        # formula would eventually disagree.
        A(f"| ${sz:,} | {s * 1e4:.2f} bps | {b_ * 1e4:.2f} bps | "
          f"{dex:.1f} bps | ~{round_trip_hurdle_bps(sz):.0f} bps |")
    A("")
    A("\\* cheapest venue's one-way DEX cost **at that same size** + taker fee "
      "+ the worse of the two Bybit slippages. Indicative only -- the real "
      "per-row computation uses each block's own quote and each direction's own "
      "slippage, and never mixes sizes.")
    A("")
    # Composition of the hurdle at each end of the ladder, computed rather than
    # asserted -- an earlier draft claimed the on-chain leg was "essentially the
    # whole hurdle", which the numbers do not support even at $1K.
    lo_sz, hi_sz = min(cheapest), max(cheapest)
    def _share(sz: int) -> tuple[float, float, float]:
        row = slips.filter(pl.col("size_usd") == sz).row(0, named=True)
        slip = max(row["sell_slip"], row["buy_slip"]) * 1e4
        # Denominator from the shared helper so these percentages are shares of
        # the same total the table above prints.
        return cheapest[sz], slip, round_trip_hurdle_bps(sz)
    d_lo, s_lo, t_lo = _share(lo_sz)
    d_hi, s_hi, t_hi = _share(hi_sz)
    A(f"The composition shifts with size. At ${lo_sz:,} the on-chain leg is "
      f"{d_lo / t_lo:.0%} of the hurdle and Bybit depth is nearly free "
      f"({s_lo:.1f} bps); at ${hi_sz:,} Bybit slippage alone ({s_hi:.0f} bps) "
      f"exceeds the *entire* ${lo_sz:,} hurdle of {t_lo:.0f} bps, and the total "
      f"is {t_hi / t_lo:.1f}x larger. Both legs deteriorate with size, so the "
      f"hurdle grows on both sides at once -- which is why a single headline "
      f"size would have been misleading and the ladder is reported in full.")
    A("")

    A("## Profitable-window duration")
    A("")
    if summary.is_empty():
        A("_No windows to characterise._")
    else:
        A("| venue | dir | size | windows | % of time | median | p95 | p99 | "
          "max | uncaptured $ | windows w/ a swap |")
        A("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in summary.iter_rows(named=True):
            A(f"| {r['pool_name']} | {r['direction']} | ${r['size_usd']:,} | "
              f"{r['n_windows']:,} | {r['pct_time_profitable']:.4f}% | "
              f"{_fmt_dur(r['dur_median_ms'])} | {_fmt_dur(r['dur_p95_ms'])} | "
              f"{_fmt_dur(r['dur_p99_ms'])} | {_fmt_dur(r['dur_max_ms'])} | "
              f"${r['uncaptured_usd']:,.0f} | "
              f"{r['windows_with_a_swap']:,}/{r['n_windows']:,} |")
    A("")
    A("Direction A = buy MNT on the DEX, sell on Bybit. "
      "Direction B = sell MNT on the DEX, buy back on Bybit.")
    A("")

    A("### How many of those windows could actually be taken")
    A("")
    A(f"A window is only an opportunity if a bot could see it and land a "
      f"transaction inside it. Mantle blocks are 2s and inclusion in the very "
      f"next block is not guaranteed, so **{ACTIONABLE_MS / 1000:.0f}s is the "
      f"optimistic floor**. Below it the window is arithmetic, not opportunity. "
      f"Splitting the table above on that floor:")
    A("")
    if summary.is_empty():
        A("_No windows to characterise._")
    else:
        A("| venue | dir | size | all windows | >= "
          f"{ACTIONABLE_MS / 1000:.0f}s | share | % of time (actionable) | "
          "uncaptured $ (actionable) |")
        A("|---|---|---|---|---|---|---|---|")
        for r in summary.iter_rows(named=True):
            n, na = r["n_windows"], r["n_actionable"]
            A(f"| {r['pool_name']} | {r['direction']} | ${r['size_usd']:,} | "
              f"{n:,} | {na:,} | {na / n if n else 0:.1%} | "
              f"{r['pct_time_actionable']:.4f}% | "
              f"${r['uncaptured_actionable_usd']:,.0f} |")
        A("")
        tw = int(summary["n_windows"].sum())
        ta = int(summary["n_actionable"].sum())
        t_all = float(summary["total_ms"].sum())
        t_act = float(summary["actionable_ms"].sum())
        u_all = float(summary["uncaptured_usd"].sum())
        u_act = float(summary["uncaptured_actionable_usd"].sum())
        A(f"Across every combination, {ta:,} of {tw:,} windows "
          f"({ta / tw if tw else 0:.1%}) clear the floor. The remainder are "
          f"single-print flickers: real in the arithmetic, unreachable in "
          f"practice.")
        A("")
        A(f"Count and duration discount very differently, and conflating them "
          f"is the easiest way to misread this table. Dropping the flickers "
          f"removes {1 - ta / tw if tw else 0:.0%} of the window COUNT but only "
          f"{1 - t_act / t_all if t_all else 0:.0%} of profitable TIME -- the "
          f"flickers are numerous and nearly durationless. Uncaptured profit "
          f"follows the count, not the time, because each window is one round "
          f"trip: ${u_all:,.0f} falls to **${u_act:,.0f}**. So the correct "
          f"reading is that the spread is open about as *long* as the headline "
          f"says, but can be *acted on* far fewer times than the window count "
          f"implies.")
    A("")

    A("## Inventory: the actual go/no-go")
    A("")
    A(f"This is not a flash-loan strategy. Both legs must fire at once, so "
      f"capital sits pre-positioned on both venues -- roughly "
      f"{INVENTORY_CAPITAL_MULT:.0f}x the trade size, since you hold both sides "
      f"of the pair. That capital has a carrying cost whether or not a spread "
      f"appears, so the only figure that decides anything is actionable "
      f"uncaptured profit *at a given size* against carry *at that same size*. "
      f"Comparing a $1K rung's profit to a $100K rung's carry, in either "
      f"direction, is how this kind of study talks itself into a business that "
      f"is not there.")
    A("")
    if summary.is_empty():
        A("_No windows, so nothing to weigh against carry._")
    else:
        A(f"| size | capital deployed | carry @ {INVENTORY_APR:.0%} APR over "
          f"{span_days:.1f}d | actionable uncaptured $ | verdict |")
        A("|---|---|---|---|---|")
        ratios: dict[int, float] = {}
        for r in carry_t.iter_rows(named=True):
            ratios[r["size_usd"]] = r["ratio"]
            if r["u"] <= 0:
                verdict = "**no profit at all**"
            elif r["ratio"] >= 2:
                verdict = f"clears carry {r['ratio']:.1f}x"
            elif r["ratio"] > 1:
                verdict = f"marginal ({r['ratio']:.2f}x carry)"
            else:
                verdict = f"**{1 / r['ratio']:.2f}x short of carry**"
            A(f"| ${r['size_usd']:,} | ${r['capital']:,.0f} | "
              f"${r['carry']:,.2f} | ${r['u']:,.0f} | {verdict} |")
        A("")
        A("This sums both venues and both directions at each size, on the "
          "assumption that one pre-positioned book can serve all of them -- "
          "which is generous to the strategy, since it counts every combination "
          "against a single carrying cost.")
        A("")
        na = int(summary["n_actionable"].sum())
        nc = int(summary["n_actionable_contested"].sum())
        # Which rungs survive a haircut for lost races. Derived, not asserted:
        # an earlier draft carried a hardcoded multiple from a short smoke run.
        halved = [sz for sz, r in ratios.items() if r >= 2.0]
        surv = (f"only {_rungs(halved, list(ratios))} still clears it"
                if halved else "no rung clears it at any size")
        A(f"**Two more reasons to read a passing verdict as a ceiling, not a "
          f"forecast.** First, the uncaptured column assumes you win *every* "
          f"actionable window; in fact {nc:,} of {na:,} "
          f"({nc / na if na else 0:.0%}) already had another party's swap land "
          f"inside them, so those were races, not gifts. Halve every figure for "
          f"a 50% win rate and {surv} -- a verdict below 2x carry does not "
          f"survive contact with competition. Second, this window is one "
          f"particular {span_days:.0f} days of one particular pair; nothing "
          f"here establishes that the spread recurs at the same rate next month.")
    A("")

    A("## Method and its limits")
    A("")
    A("- DEX prices come from quoting the live contracts (Agni QuoterV2, "
      "Merchant Moe `getSwapOut`) at every block where pool state changed. "
      "Between those blocks the state provably does not move, so the curve is "
      "exact rather than sampled.")
    A("- Because of that, event-driven sampling bias is avoided: we are not "
      "only looking at blocks where somebody traded (1.3% of blocks), which is "
      "precisely the subset that would hide 'a spread existed and nobody took "
      "it'.")
    A("- Bybit L1 is reconstructed from the public tape: taker-side `buy` "
      "prints mark the ask, `sell` prints the bid. This can only be wider than "
      "the true book, never tighter. RPI prints are excluded because that "
      "liquidity is not reachable by a bot.")
    A("- Venue alignment is strictly at-or-before: a DEX block at time T may "
      "only see Bybit prints published at or before T.")
    A("- **Main approximation:** Bybit depth beyond L1 comes from a single live "
      "orderbook snapshot, applied as a multiplicative slippage-vs-mid curve "
      "across the whole window. Historical L2 is not published. The book's "
      "*shape* is assumed stable; its absolute price is not.")
    A("- Gas is charged per swap from each block's own base fee plus the "
      "OP-Stack L1 data fee, and is a rounding error (~0.05 bps at $1K).")
    gap_med, gap_tw, cov = _resolution()
    A(f"- **Time resolution is coarser than the actionable floor, and this is "
      f"the weakest part of the duration measurement.** Profitability is "
      f"evaluated at Bybit print times. Prints are bursty: the median gap is "
      f"{gap_med:.0f}ms, but weighted by the time it actually covers the mean "
      f"gap is {gap_tw:.0f}s, because quiet stretches dominate the clock. So a "
      f"duration near the {ACTIONABLE_MS / 1000:.0f}s floor carries granularity "
      f"error of that order, in both directions: some sub-floor flickers are "
      f"really longer windows seen once, and some multi-second windows are a "
      f"single quiet-period gap. Treat the >= {ACTIONABLE_MS / 1000:.0f}s split "
      f"as a bucket, not a measurement.")
    A(f"- Relatedly, {cov:.0%} of elapsed time sits in DEX intervals that no "
      f"print falls inside, so they are never evaluated at all. They are short "
      f"by construction (rapid-fire state changes that resolve before the next "
      f"print) and therefore below the actionable floor anyway, but they are a "
      f"true gap in coverage rather than a measured zero.")
    A("- Out of scope by design: order placement, multi-hop/triangular routes, "
      "MEV and gas-auction dynamics, realtime operation.")
    A("")

    REPORT_MD.write_text("\n".join(L) + "\n")


def _dex_cost_by_size() -> pl.DataFrame:
    """Median one-way DEX cost vs the pool's own mid, per (pool, size).

    Both display tables derive from this, so the headline cost figures and the
    size-matched hurdle can never drift apart.
    """
    from .m2_quotes import DEX_QUOTES
    q = pl.read_parquet(DEX_QUOTES).filter(
        pl.col("quote_ok") & (pl.col("amount_in_left") == 0))
    q = q.with_columns(
        pl.when(pl.col("direction") == "A")
          .then(pl.col("amount_in") / 1e6 / (pl.col("amount_out") / 1e18))
          .otherwise((pl.col("amount_out") / 1e6) / (pl.col("amount_in") / 1e18))
          .alias("eff")
    ).with_columns(
        pl.when(pl.col("direction") == "A")
          .then((pl.col("eff") / pl.col("mid_price") - 1) * 1e4)
          .otherwise((1 - pl.col("eff") / pl.col("mid_price")) * 1e4)
          .alias("cost_bps")
    )
    return q.group_by("pool_name", "size_usd").agg(
        pl.col("cost_bps").median().alias("cost_bps")
    ).sort("pool_name", "size_usd")


def _dex_cost_table() -> pl.DataFrame:
    """The per-size costs pivoted to the three display columns."""
    by = _dex_cost_by_size()
    return by.group_by("pool_name").agg(
        pl.col("cost_bps").filter(pl.col("size_usd") == 1_000).first().alias("c1k"),
        pl.col("cost_bps").filter(pl.col("size_usd") == 10_000).first().alias("c10k"),
        pl.col("cost_bps").filter(pl.col("size_usd") == 100_000).first().alias("c100k"),
    ).sort("pool_name")


def _bybit_spread() -> float:
    return float(pl.read_parquet(BYBIT_QUOTES)["spread_bps"].median())


def _resolution() -> tuple[float, float, float]:
    """How finely the Bybit tape actually resolves time, and what it misses.

    Returns (median print gap ms, time-weighted mean gap s, fraction of elapsed
    DEX-interval time containing no print at all).

    The time-weighted mean is the honest figure for "how precisely can a window
    boundary be located": a randomly chosen instant falls inside a gap with
    probability proportional to that gap's length, so gaps must be weighted by
    themselves, not counted. The tape is bursty enough that the two differ by
    three orders of magnitude -- reporting only the median would make the
    resolution look far better than it is.
    """
    ts = pl.read_parquet(BYBIT_QUOTES).select(
        pl.col("timestamp").alias("ts")).sort("ts")
    gaps = ts.select(pl.col("ts").diff().cast(pl.Float64).alias("g")).drop_nulls()
    tw = float((gaps["g"] ** 2).sum() / gaps["g"].sum()) / 1000

    iv = pl.read_parquet(INTERVALS)
    miss_ms = total_ms = 0.0
    for pool in sorted(iv["pool_name"].unique()):
        blk = (iv.filter(pl.col("pool_name") == pool)
                 .select("block_number", "ts_ms").unique().sort("ts_ms"))
        p = ts.filter(pl.col("ts").is_between(blk["ts_ms"].min(),
                                              blk["ts_ms"].max()))
        seen = set(p.join_asof(blk, left_on="ts", right_on="ts_ms",
                               strategy="backward")["block_number"].to_list())
        d = blk.with_columns(
            (pl.col("ts_ms").shift(-1) - pl.col("ts_ms")).alias("dur"),
            pl.col("block_number").is_in(list(seen)).alias("seen"),
        ).drop_nulls("dur")
        total_ms += float(d["dur"].sum())
        miss_ms += float(d.filter(~pl.col("seen"))["dur"].sum())
    return (float(gaps["g"].median()), tw,
            miss_ms / total_ms if total_ms else 0.0)


def main() -> None:
    argparse.ArgumentParser().parse_args()
    run()


if __name__ == "__main__":
    main()
