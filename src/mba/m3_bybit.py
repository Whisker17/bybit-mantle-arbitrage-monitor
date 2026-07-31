"""M3 -- Bybit spot MNTUSDT tape, reconstructed quotes, and a slippage curve.

Three artifacts:

  bybit_trades.parquet  every public print (ts_ms, price, volume, side, rpi)
  bybit_quotes.parquet  approximate best bid/ask carried forward from the tape,
                        each with the age of the print it came from
  bybit_depth.parquet   VWAP cost vs mid for the size ladder, from a *live*
                        orderbook snapshot

Why quotes are reconstructed rather than downloaded: Bybit publishes no
historical L1/L2 for spot. `side` is the TAKER side, so a `buy` print executed
at or above the ask and a `sell` print at or below the bid. Carrying each
forward gives a bid/ask envelope that is conservative by construction -- it can
only ever be wider than the true book, never tighter.

Two honesty controls:

  * RPI prints are excluded from quote reconstruction. Retail Price Improvement
    fills execute inside the visible spread on liquidity a bot cannot reach;
    counting them would narrow the apparent spread and inflate arb profit.
  * Every quote carries bid_age_ms / ask_age_ms. A quote inferred from a print
    ten minutes old is not evidence about the book now, and M5 must be able to
    say so instead of silently treating it as fact.

The depth curve is a single live snapshot applied across the whole window --
the one genuinely unavoidable approximation in this pipeline. It is written to
its own file so the report can state it as an assumption rather than bury it.
"""

from __future__ import annotations

import argparse
import gzip
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import httpx
import polars as pl

from .config import (BYBIT_REST, BYBIT_SYMBOL, BYBIT_TICK_URL, DATA,
                     SIZE_LADDER_USD)

CACHE = DATA / "bybit"
TRADES = DATA / "bybit_trades.parquet"
QUOTES = DATA / "bybit_quotes.parquet"
DEPTH = DATA / "bybit_depth.parquet"
BOOK = DATA / "bybit_book.parquet"

# NOTE for M4: the slippage in bybit_depth.parquet is measured from MID and so
# already contains the half-spread (at $1K it is 1.29 bps against a 2.51 bps
# book -- i.e. exactly half). Combine it with the reconstructed mid, never with
# bid/ask, or the spread gets charged twice.

SCHEMA = {"id": pl.Int64, "timestamp": pl.Int64, "price": pl.Float64,
          "volume": pl.Float64, "side": pl.String, "rpi": pl.Int8}


def _fetch_day(client: httpx.Client, day: date) -> bytes | None:
    """Return raw CSV bytes for one day, cached on disk. None if not published."""
    CACHE.mkdir(parents=True, exist_ok=True)
    dst = CACHE / f"{BYBIT_SYMBOL}_{day}.csv.gz"
    if dst.exists() and dst.stat().st_size > 1024:
        return gzip.decompress(dst.read_bytes())
    url = BYBIT_TICK_URL.format(sym=BYBIT_SYMBOL, date=day)
    r = client.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    dst.write_bytes(r.content)
    return gzip.decompress(r.content)


def download(days: int) -> pl.DataFrame:
    """Daily files for the trailing `days` days, oldest first."""
    today = date.today()
    wanted = [today - timedelta(days=i) for i in range(days, -1, -1)]
    frames: list[pl.DataFrame] = []
    missing: list[date] = []

    with httpx.Client(timeout=120.0, follow_redirects=True,
                      headers={"user-agent": "mba/0.1"}) as client:
        # 4 at a time: polite to a public static host, still ~8x serial.
        with ThreadPoolExecutor(max_workers=4) as pool:
            blobs = list(pool.map(lambda d: (d, _fetch_day(client, d)), wanted))

    for day, blob in blobs:
        if blob is None:
            missing.append(day)
            continue
        df = pl.read_csv(io.BytesIO(blob), schema_overrides=SCHEMA)
        frames.append(df.with_columns(pl.lit(str(day)).alias("day")))

    if missing:
        print(f"  not published (expected for today / pre-listing): "
              f"{', '.join(str(d) for d in missing)}")
    if not frames:
        raise SystemExit("ABORT: no Bybit tick files downloaded at all.")

    trades = pl.concat(frames, how="diagonal").sort("timestamp")
    print(f"  {len(trades):,} prints over {len(frames)} days "
          f"({trades['timestamp'].min()} .. {trades['timestamp'].max()} ms)")
    return trades


def build_quotes(trades: pl.DataFrame) -> pl.DataFrame:
    """Carry the last non-RPI buy print forward as the ask, sell as the bid."""
    t = trades.filter(pl.col("rpi") == 0).sort("timestamp")
    q = t.with_columns(
        pl.when(pl.col("side") == "buy").then(pl.col("price")).alias("_ask"),
        pl.when(pl.col("side") == "buy").then(pl.col("timestamp")).alias("_ask_ts"),
        pl.when(pl.col("side") == "sell").then(pl.col("price")).alias("_bid"),
        pl.when(pl.col("side") == "sell").then(pl.col("timestamp")).alias("_bid_ts"),
    ).with_columns(
        pl.col("_ask").forward_fill().alias("ask"),
        pl.col("_ask_ts").forward_fill().alias("ask_ts"),
        pl.col("_bid").forward_fill().alias("bid"),
        pl.col("_bid_ts").forward_fill().alias("bid_ts"),
    ).drop("_ask", "_ask_ts", "_bid", "_bid_ts")

    q = q.filter(pl.col("ask").is_not_null() & pl.col("bid").is_not_null())
    q = q.with_columns(
        (pl.col("timestamp") - pl.col("ask_ts")).alias("ask_age_ms"),
        (pl.col("timestamp") - pl.col("bid_ts")).alias("bid_age_ms"),
        ((pl.col("bid") + pl.col("ask")) / 2).alias("mid"),
    ).with_columns(
        ((pl.col("ask") - pl.col("bid")) / pl.col("mid") * 1e4).alias("spread_bps"),
        pl.max_horizontal("ask_age_ms", "bid_age_ms").alias("quote_age_ms"),
    )

    # A crossed envelope (bid > ask) is not a real crossed book -- it means one
    # side's last print is stale. Keep the rows, flag them, and let M4 decide.
    q = q.with_columns((pl.col("bid") > pl.col("ask")).alias("crossed"))

    n = len(q)
    xed = int(q["crossed"].sum())
    print(f"  {n:,} quote rows | crossed(stale) {xed:,} = {xed / n:.2%}")
    print(f"  spread_bps  median {q['spread_bps'].median():.2f}  "
          f"p90 {q['spread_bps'].quantile(0.90):.2f}")
    print(f"  quote_age_ms median {q['quote_age_ms'].median():.0f}  "
          f"p95 {q['quote_age_ms'].quantile(0.95):.0f}  "
          f"max {q['quote_age_ms'].max():.0f}")
    return q.select("timestamp", "bid", "ask", "mid", "spread_bps",
                    "bid_age_ms", "ask_age_ms", "quote_age_ms", "crossed")


def _vwap(levels: list[tuple[float, float]], usd: float, side: str
          ) -> tuple[float | None, float]:
    """Walk the book for `usd` notional. Returns (vwap, filled_usd)."""
    spent = 0.0
    qty = 0.0
    for px, sz in levels:
        avail_usd = px * sz
        take_usd = min(avail_usd, usd - spent)
        if take_usd <= 0:
            break
        qty += take_usd / px
        spent += take_usd
        if spent >= usd - 1e-9:
            break
    if qty == 0:
        return None, 0.0
    return spent / qty, spent


def fetch_depth() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Live orderbook snapshot -> (ladder VWAP costs, raw levels).

    The raw levels are persisted too so M4 can evaluate exact VWAP at any Q when
    solving for the optimal size, instead of interpolating between ladder rungs.
    """
    with httpx.Client(timeout=30.0, headers={"user-agent": "mba/0.1"}) as c:
        r = c.get(f"{BYBIT_REST}/v5/market/orderbook",
                  params={"category": "spot", "symbol": BYBIT_SYMBOL,
                          "limit": 200})
        r.raise_for_status()
        ob = r.json()["result"]

    bids = [(float(p), float(s)) for p, s in ob["b"]]
    asks = [(float(p), float(s)) for p, s in ob["a"]]
    if not bids or not asks:
        raise SystemExit("ABORT: empty Bybit orderbook snapshot.")
    mid = (bids[0][0] + asks[0][0]) / 2
    top_spread = (asks[0][0] - bids[0][0]) / mid * 1e4
    depth_bid = sum(p * s for p, s in bids)
    depth_ask = sum(p * s for p, s in asks)
    print(f"  snapshot mid {mid:.6f}  top spread {top_spread:.2f} bps  "
          f"book depth ${depth_bid:,.0f} bid / ${depth_ask:,.0f} ask")

    rows = []
    for usd in SIZE_LADDER_USD:
        # We SELL MNT into the bids (direction A exit) and BUY from the asks
        # (direction B exit).
        sell_vwap, sell_filled = _vwap(bids, usd, "bid")
        buy_vwap, buy_filled = _vwap(asks, usd, "ask")
        rows.append({
            "size_usd": usd,
            "mid": mid,
            "sell_vwap": sell_vwap,
            "sell_slip_bps": None if sell_vwap is None
            else (1 - sell_vwap / mid) * 1e4,
            "sell_filled_usd": sell_filled,
            "buy_vwap": buy_vwap,
            "buy_slip_bps": None if buy_vwap is None
            else (buy_vwap / mid - 1) * 1e4,
            "buy_filled_usd": buy_filled,
            "book_depth_bid_usd": depth_bid,
            "book_depth_ask_usd": depth_ask,
        })
    d = pl.DataFrame(rows)
    print("\n  Bybit slippage vs mid from live book (bps):")
    print(d.select("size_usd", "sell_slip_bps", "buy_slip_bps",
                   "sell_filled_usd", "buy_filled_usd")
           .with_columns(pl.col("^.*_bps$").round(2),
                         pl.col("^.*_usd$").round(0)))

    levels = pl.DataFrame(
        [{"side": "bid", "level": i, "price": p, "size": s}
         for i, (p, s) in enumerate(bids)]
        + [{"side": "ask", "level": i, "price": p, "size": s}
           for i, (p, s) in enumerate(asks)]
    ).with_columns(pl.lit(mid).alias("snapshot_mid"))
    return d, levels


def run(days: int) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    print("downloading Bybit tape...")
    trades = download(days)
    trades.write_parquet(TRADES)

    print("\nreconstructing quotes...")
    quotes = build_quotes(trades)
    quotes.write_parquet(QUOTES)

    print("\nfetching live depth...")
    depth, levels = fetch_depth()
    depth.write_parquet(DEPTH)
    levels.write_parquet(BOOK)

    print(f"\nwrote {TRADES.name} ({len(trades):,}), "
          f"{QUOTES.name} ({len(quotes):,}), {DEPTH.name} ({len(depth)}), "
          f"{BOOK.name} ({len(levels)} levels)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=31,
                    help="trailing days of daily tick files to pull")
    run(days=ap.parse_args().days)


if __name__ == "__main__":
    main()
