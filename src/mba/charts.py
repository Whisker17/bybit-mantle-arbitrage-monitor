"""PNG charts for the M5 report.

Kept separate from M5 so a plotting failure can never take down the numbers --
M5 catches import/render errors and still writes the Markdown.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # headless: no display, no interactive backend
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

from .config import REPORT, SIZE_LADDER_USD  # noqa: E402

PALETTE = {"agni_v3": "#2b6cb0", "moe_lb": "#c05621"}


def _save(fig, name: str) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    path = REPORT / name
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    print(f"    chart {path.name}")


def chart_cost_vs_hurdle(iv: pl.DataFrame) -> None:
    """The single most explanatory chart: hurdle vs the dislocation available."""
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
    fig, ax = plt.subplots(figsize=(8, 5))
    for pool in sorted(q["pool_name"].unique()):
        g = (q.filter(pl.col("pool_name") == pool)
              .group_by("size_usd").agg(pl.col("cost_bps").median())
              .sort("size_usd"))
        ax.plot(g["size_usd"], g["cost_bps"], marker="o",
                color=PALETTE.get(pool, None), label=f"{pool} one-way DEX cost")
    ax.set_xscale("log")
    ax.set_xlabel("notional (USD)")
    ax.set_ylabel("cost vs mid (bps)")
    ax.set_title("On-chain execution cost sets the arbitrage floor")
    ax.grid(alpha=0.3)
    ax.legend()
    _save(fig, "cost_vs_size.png")


def chart_duration_cdf(windows: pl.DataFrame) -> None:
    if windows.is_empty():
        return
    fig, ax = plt.subplots(figsize=(8, 5))
    for pool in sorted(windows["pool_name"].unique()):
        for size in [1_000, 10_000, 100_000]:
            w = windows.filter((pl.col("pool_name") == pool)
                               & (pl.col("size_usd") == size))
            if w.is_empty():
                continue
            d = np.sort(w["duration_ms"].to_numpy() / 1000)
            y = np.arange(1, len(d) + 1) / len(d)
            ax.step(d, y, where="post",
                    label=f"{pool} ${size // 1000}K (n={len(d)})")
    ax.set_xscale("log")
    ax.set_xlabel("window duration (seconds, log scale)")
    ax.set_ylabel("cumulative fraction of windows")
    ax.set_title("How long profitable windows stayed open")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    _save(fig, "duration_cdf.png")


def chart_pct_time(summary: pl.DataFrame) -> None:
    if summary.is_empty():
        return
    fig, ax = plt.subplots(figsize=(9, 5))
    combos = (summary.select("pool_name", "direction").unique()
                     .sort(["pool_name", "direction"]).to_dicts())
    x = np.arange(len(SIZE_LADDER_USD))
    width = 0.8 / max(len(combos), 1)
    for i, c in enumerate(combos):
        s = summary.filter((pl.col("pool_name") == c["pool_name"])
                           & (pl.col("direction") == c["direction"]))
        vals = [
            (s.filter(pl.col("size_usd") == sz)["pct_time_profitable"].sum()
             if not s.filter(pl.col("size_usd") == sz).is_empty() else 0.0)
            for sz in SIZE_LADDER_USD
        ]
        ax.bar(x + i * width, vals, width,
               label=f"{c['pool_name']} dir {c['direction']}")
    ax.set_xticks(x + 0.4 - width / 2)
    ax.set_xticklabels([f"${s // 1000}K" for s in SIZE_LADDER_USD])
    ax.set_ylabel("% of elapsed time profitable")
    ax.set_title("Time-weighted share of the window with a live opportunity")
    ax.grid(alpha=0.3, axis="y")
    ax.legend(fontsize=8)
    _save(fig, "pct_time_profitable.png")


def chart_profit_timeseries(iv: pl.DataFrame, size: int = 5_000) -> None:
    """Net profit at the contemporaneous Bybit mid, per venue, over the window."""
    fresh = iv.filter(~pl.col("stale"))
    if fresh.is_empty():
        print("    !! profit_timeseries skipped: no fresh rows")
        return
    # Fall back to the nearest available ladder size rather than silently
    # producing no chart at all if `size` was not run.
    avail = sorted(fresh["size_usd"].unique().to_list())
    if size not in avail:
        size = min(avail, key=lambda s: abs(s - size))
        print(f"    profit_timeseries: falling back to ${size:,}")
    sub = fresh.filter(pl.col("size_usd") == size)
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    for ax, direction in zip(axes, ["A", "B"]):
        for pool in sorted(sub["pool_name"].unique()):
            g = sub.filter((pl.col("pool_name") == pool)
                           & (pl.col("direction") == direction)).sort("ts_ms")
            if g.is_empty():
                continue
            t = g["ts_ms"].to_numpy() / 86_400_000
            t = t - t.min()
            ax.plot(t, g["net_profit_bps"].to_numpy(), lw=0.4,
                    color=PALETTE.get(pool, None), label=pool)
        ax.axhline(0, color="k", lw=1)
        ax.set_ylabel("net profit (bps)")
        ax.set_title(f"direction {direction} at ${size:,}", fontsize=10)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    axes[-1].set_xlabel("days into window")
    fig.suptitle("Net arbitrage profit after all costs "
                 "(zero line = breakeven)", fontsize=11)
    _save(fig, "profit_timeseries.png")


def make_charts(windows: pl.DataFrame, summary: pl.DataFrame,
                iv: pl.DataFrame) -> None:
    chart_cost_vs_hurdle(iv)
    chart_profit_timeseries(iv)
    chart_duration_cdf(windows)
    chart_pct_time(summary)
