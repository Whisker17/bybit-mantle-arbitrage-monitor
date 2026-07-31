"""M4 -- align the two venues in time and compute net profit.

Output artifacts:

  opportunities.parquet  one row per (DEX state change x direction x size), with
                         the contemporaneous Bybit quote ASOF-joined on, net
                         profit in USD and bps, and the breakeven Bybit mid
  intervals.parquet      the same rows reduced to a step function -- validity
                         window [ts_ms, ts_end_ms) plus breakeven_mid -- which is
                         what M5 intersects with the Bybit tape to measure
                         profitable-window DURATION

The breakeven trick
-------------------
For direction A (buy MNT on the DEX, sell it on Bybit) the DEX leg fixes a
quantity of MNT for a fixed USD outlay, and that pair does not change until the
pool's next state change. So

    profit(t) = qty_mnt * mid(t) * (1-slip) * (1-fee) - usd_in - gas

is affine in the Bybit mid with a CONSTANT slope over the interval, and

    profit(t) > 0   <=>   mid(t) > breakeven_mid

where breakeven_mid is constant on the interval. Direction B inverts the
inequality. This makes "was there a profitable window, and for how long" an
exact comparison of a continuous series against a step function, rather than a
24-way cross join of 2M Bybit prints against every DEX interval. It is not an
approximation -- it is the same arithmetic, factored.

Time alignment
--------------
The join is strictly at-or-before (Polars `join_asof`, backward): a DEX block at
time T may only see a Bybit print published at or before T. Anything else leaks
future information into the past and manufactures profit. `bybit_lag_ms` records how stale the matched print
was, and `stale` flags the rows where that exceeds MAX_BYBIT_LAG_MS -- flagged,
not dropped, so M5 can report coverage honestly.
"""

from __future__ import annotations

import argparse
import functools

import polars as pl

from .config import (BYBIT_TAKER_FEE, DATA, GAS_UNITS, INVENTORY_APR,
                     INVENTORY_CAPITAL_MULT, L1_FEE_USD, MAX_BYBIT_LAG_MS,
                     SIZE_LADDER_USD, USDT0_DECIMALS, WMNT_DECIMALS)
from .m3_bybit import BOOK, QUOTES as BYBIT_QUOTES
from .m2_quotes import DEX_QUOTES

OPPS = DATA / "opportunities.parquet"
INTERVALS = DATA / "intervals.parquet"


# --- Bybit execution price from the live book ---------------------------
def book_levels() -> tuple[list[tuple[float, float]], list[tuple[float, float]], float]:
    b = pl.read_parquet(BOOK)
    mid = float(b["snapshot_mid"][0])
    bids = [(r["price"], r["size"]) for r in
            b.filter(pl.col("side") == "bid").sort("level").iter_rows(named=True)]
    asks = [(r["price"], r["size"]) for r in
            b.filter(pl.col("side") == "ask").sort("level").iter_rows(named=True)]
    return bids, asks, mid


def slip_fraction(levels: list[tuple[float, float]], usd: float, snapshot_mid: float,
                  is_bid: bool) -> float | None:
    """Fractional slippage vs mid for `usd` notional walking `levels`.

    Measured from mid, so it ALREADY includes crossing the half-spread. Returned
    as a fraction of mid and applied multiplicatively to the historical mid --
    i.e. we assume the *shape* of the book is stable over the window even though
    its absolute price is not. That is the one unavoidable approximation in this
    pipeline; it is stated in the report rather than hidden.
    """
    spent = qty = 0.0
    for px, sz in levels:
        take = min(px * sz, usd - spent)
        if take <= 0:
            break
        qty += take / px
        spent += take
        if spent >= usd - 1e-9:
            break
    if qty == 0 or spent < usd - 1e-6:
        return None  # book cannot absorb this size at all
    vwap = spent / qty
    return (1 - vwap / snapshot_mid) if is_bid else (vwap / snapshot_mid - 1)


def slip_table() -> pl.DataFrame:
    bids, asks, mid = book_levels()
    rows = []
    for usd in SIZE_LADDER_USD:
        rows.append({
            "size_usd": usd,
            # direction A exits by SELLING MNT into the bids
            "sell_slip": slip_fraction(bids, usd, mid, is_bid=True),
            # direction B exits by BUYING MNT from the asks
            "buy_slip": slip_fraction(asks, usd, mid, is_bid=False),
        })
    t = pl.DataFrame(rows)
    if t["sell_slip"].null_count() or t["buy_slip"].null_count():
        print("  !! book cannot absorb some ladder sizes; those rows will be "
              "marked unfillable rather than assumed free")
    return t


@functools.lru_cache(maxsize=None)
def round_trip_hurdle_bps(size_usd: int) -> float | None:
    """Indicative round-trip cost at one ladder size, in bps.

    Cheapest venue's one-way DEX cost at that SAME size + Bybit taker fee + the
    worse of the two Bybit slippages. Lives here rather than in either report so
    that M5's hurdle table and M6's "how much of the hurdle does venue choice
    remove" claim cannot quote different numbers for the same thing.

    Returns None if the book cannot absorb that size or the pools were not
    quoted at it.
    """
    from .m5_report import _dex_cost_by_size  # report-side helper, no cycle
    row = slip_table().filter(pl.col("size_usd") == size_usd)
    if row.is_empty():
        return None
    s, b = row["sell_slip"][0], row["buy_slip"][0]
    if s is None or b is None:
        return None
    dex = (_dex_cost_by_size().filter(pl.col("size_usd") == size_usd)["cost_bps"]
           .min())
    if dex is None:
        return None
    return float(dex) + BYBIT_TAKER_FEE * 1e4 + max(s, b) * 1e4


def run(max_lag_ms: int = MAX_BYBIT_LAG_MS) -> None:
    dq = pl.read_parquet(DEX_QUOTES)
    slips = slip_table()

    # Effective DEX leg, in human units. Direction A: usd_in -> qty_mnt.
    # Direction B: qty_mnt -> usd_out.
    dq = dq.filter(pl.col("quote_ok") & (pl.col("amount_in_left") == 0))
    # M2 records the block header timestamp in seconds; Bybit prints are in
    # milliseconds. Align units before any join touches them.
    dq = dq.with_columns((pl.col("block_timestamp") * 1000).alias("ts_ms"))
    # Raw wei amounts exceed Int64 (direction B at $100K is ~2.4e23 wei), so
    # Polars types them Int128, which Arrow cannot hand to DuckDB. Cast to f64:
    # at 1e23 the mantissa still resolves ~1e7 wei, i.e. 1e-11 MNT.
    dq = dq.with_columns(
        pl.col("amount_in", "amount_out", "amount_in_left", "gas_estimate")
          .cast(pl.Float64))
    dq = dq.with_columns(
        pl.when(pl.col("direction") == "A")
          .then(pl.col("amount_in") / 10 ** USDT0_DECIMALS)
          .otherwise(pl.col("amount_out") / 10 ** USDT0_DECIMALS).alias("usd_leg"),
        pl.when(pl.col("direction") == "A")
          .then(pl.col("amount_out") / 10 ** WMNT_DECIMALS)
          .otherwise(pl.col("amount_in") / 10 ** WMNT_DECIMALS).alias("qty_mnt"),
    )

    # Gas, in USD, from this block's own base fee and this venue's measured units.
    gas_units = pl.col("pool_name").replace_strict(
        GAS_UNITS, return_dtype=pl.Int64)
    dq = dq.with_columns(
        (gas_units * pl.col("base_fee_wei") / 1e18 * pl.col("mid_price")
         + L1_FEE_USD).alias("gas_usd")
    )

    dq = dq.join(slips, on="size_usd", how="left")

    # Breakeven Bybit mid: the price at which this DEX quote exactly pays for
    # itself. Constant across the interval, which is what makes M5 cheap.
    dq = dq.with_columns(
        pl.when(pl.col("direction") == "A")
          .then((pl.col("usd_leg") + pl.col("gas_usd"))
                / (pl.col("qty_mnt") * (1 - pl.col("sell_slip"))
                   * (1 - BYBIT_TAKER_FEE)))
          .otherwise((pl.col("usd_leg") - pl.col("gas_usd"))
                     / (pl.col("qty_mnt") * (1 + pl.col("buy_slip"))
                        * (1 + BYBIT_TAKER_FEE)))
          .alias("breakeven_mid")
    )

    # Strictly at-or-before: a DEX block at time T sees the last Bybit print
    # published no later than T. Polars' join_asof(strategy="backward") is a
    # single merge pass over two sorted columns -- 212k x 2M lands in ~0.2s.
    #
    # Do NOT do this with DuckDB's ASOF JOIN. With no equality predicate to
    # partition on it falls back to a range-scan strategy, and the same join
    # burned 15+ CPU-minutes without finishing. Measured difference: ~4 orders
    # of magnitude. Same semantics, same result.
    byb = pl.read_parquet(BYBIT_QUOTES).select(
        pl.col("timestamp").alias("bybit_ts_ms"),
        pl.col("mid").alias("bybit_mid"),
        pl.col("bid").alias("bybit_bid"),
        pl.col("ask").alias("bybit_ask"),
        pl.col("spread_bps").alias("bybit_spread_bps"),
        pl.col("quote_age_ms").alias("bybit_quote_age_ms"),
        pl.col("crossed").alias("bybit_crossed"),
    ).sort("bybit_ts_ms")

    opp = dq.sort("ts_ms").join_asof(
        byb, left_on="ts_ms", right_on="bybit_ts_ms", strategy="backward"
    ).with_columns((pl.col("ts_ms") - pl.col("bybit_ts_ms")).alias("bybit_lag_ms"))

    # A DEX block earlier than the first Bybit print has nothing to compare
    # against. Dropping is right -- carrying a null mid forward would silently
    # produce null profit rows that later aggregations would read as "not
    # profitable" rather than "unknown".
    n_unmatched = int(opp["bybit_ts_ms"].null_count())
    if n_unmatched:
        print(f"  dropped {n_unmatched:,} DEX rows with no prior Bybit print")
        opp = opp.filter(pl.col("bybit_ts_ms").is_not_null())

    # Realised net profit at the contemporaneous Bybit mid.
    opp = opp.with_columns(
        pl.when(pl.col("direction") == "A")
          .then(pl.col("qty_mnt") * pl.col("bybit_mid")
                * (1 - pl.col("sell_slip")) * (1 - BYBIT_TAKER_FEE)
                - pl.col("usd_leg") - pl.col("gas_usd"))
          .otherwise(pl.col("usd_leg")
                     - pl.col("qty_mnt") * pl.col("bybit_mid")
                     * (1 + pl.col("buy_slip")) * (1 + BYBIT_TAKER_FEE)
                     - pl.col("gas_usd"))
          .alias("net_profit_usd")
    ).with_columns(
        (pl.col("net_profit_usd") / pl.col("size_usd") * 1e4).alias("net_profit_bps"),
        (pl.col("bybit_lag_ms") > max_lag_ms).alias("stale"),
        (pl.col("net_profit_usd") > 0).alias("profitable"),
    )

    opp.write_parquet(OPPS)

    # Step function for M5: each DEX quote is valid until that pool's next
    # state change. Computed per (pool, direction, size) so the shift cannot
    # bleed across series.
    iv = opp.sort(["pool_name", "direction", "size_usd", "ts_ms"]).with_columns(
        pl.col("ts_ms").shift(-1)
          .over(["pool_name", "direction", "size_usd"]).alias("ts_end_ms")
    )
    iv.select("pool_name", "direction", "size_usd", "block_number", "ts_ms",
              "ts_end_ms", "breakeven_mid", "qty_mnt", "usd_leg", "gas_usd",
              "sell_slip", "buy_slip", "bybit_mid", "net_profit_usd",
              "net_profit_bps", "profitable", "stale").write_parquet(INTERVALS)

    _summarise(opp, max_lag_ms)


def _summarise(opp: pl.DataFrame, max_lag_ms: int) -> None:
    pl.Config.set_tbl_rows(40)
    n = len(opp)
    stale = int(opp["stale"].sum())
    xed = int(opp["bybit_crossed"].sum())
    print(f"\nwrote {OPPS.name} ({n:,} rows) and {INTERVALS.name}")

    # Bybit's public archive publishes whole days, so the tape ends at the last
    # complete UTC day while the chain scan runs to the current head. DEX blocks
    # in that uncovered tail can only match the tape's final print, which makes
    # their lag hours rather than seconds. They are all stale and therefore
    # excluded from every figure below -- but reported separately, because a
    # headline "max lag 13h" otherwise reads as a hole in the data.
    tape_end = opp.filter(~pl.col("stale"))["bybit_ts_ms"].max()
    tail = opp.filter(pl.col("ts_ms") > tape_end)
    covered = opp.filter(pl.col("ts_ms") <= tape_end)
    lag = covered["bybit_lag_ms"]
    print(f"  bybit_lag_ms (within the tape, n={len(covered):,})  "
          f"median {lag.median():.0f}  p95 {lag.quantile(0.95):.0f}  "
          f"max {lag.max():.0f}")
    if not tail.is_empty():
        print(f"  + {len(tail):,} rows ({tail['block_number'].n_unique()} blocks, "
              f"{len(tail) / n:.2%}) are past the tape's last print, spanning "
              f"{(opp['ts_ms'].max() - tape_end) / 3.6e6:.1f}h -- all stale, all excluded")
    print(f"  stale (>{max_lag_ms} ms) {stale:,} = {stale / n:.2%} | "
          f"crossed-envelope {xed:,} = {xed / n:.2%}")

    fresh = opp.filter(~pl.col("stale"))
    print(f"\nnet profit at the contemporaneous Bybit mid "
          f"(fresh rows only, n={len(fresh):,}):")
    print(fresh.group_by("pool_name", "direction", "size_usd").agg(
        pl.len().alias("n"),
        pl.col("profitable").sum().alias("n_profit"),
        (pl.col("profitable").mean() * 100).round(3).alias("pct_profit"),
        pl.col("net_profit_bps").median().round(1).alias("med_bps"),
        pl.col("net_profit_bps").max().round(1).alias("max_bps"),
        pl.col("net_profit_usd").filter(pl.col("profitable")).sum()
          .round(2).alias("gross_usd"),
    ).sort(["pool_name", "direction", "size_usd"]))

    cap = INVENTORY_CAPITAL_MULT * max(SIZE_LADDER_USD)
    span_days = (opp["ts_ms"].max() - opp["ts_ms"].min()) / 86_400_000
    carry = cap * INVENTORY_APR * span_days / 365
    print(f"\ninventory hurdle over {span_days:.1f} days at "
          f"{INVENTORY_APR:.0%} APR on ${cap:,.0f} deployed: ${carry:,.2f}")
    print("  (charged at the window level, not per fill -- see module docstring)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-lag-ms", type=int, default=MAX_BYBIT_LAG_MS)
    run(max_lag_ms=ap.parse_args().max_lag_ms)


if __name__ == "__main__":
    main()
