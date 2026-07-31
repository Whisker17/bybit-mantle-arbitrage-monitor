"""M6 -- who actually traded these pools, and what did they make?

Deviation from the original plan, stated up front: this was specced as a Dune
query. It is instead computed from the raw logs M1 already fetched plus
`eth_getCode`. Same questions, answered from primary data rather than a
third-party index -- more precise (actual per-swap amounts, not bucketed),
reproducible without an API key, and consistent by construction with the rest of
the pipeline. Nothing was dropped from the original scope.

Four questions:
  1. Concentration -- is this a handful of bots or a crowd? (top-N share, HHI)
  2. Contract vs EOA -- a contract beneficiary is a bot; an EOA is likelier
     manual or a simple script.
  3. Temporal clustering -- do swaps arrive in bursts, as arbitrage would?
  4. Realized profit -- for each real swap, what would closing the other leg on
     Bybit at that moment have paid? That is the profit an arbitrageur actually
     took, as opposed to the profit that was merely available.

Decode validation (worth keeping, because the result initially looked wrong)
--------------------------------------------------------------------------
Agni recorded zero Mint/Burn in the whole window, so its liquidity is static and
the sqrtPriceX96 carried in each Swap event fully determines the price path. That
allows an exact, RPC-free audit: compare each swap's effective price against the
PREVIOUS swap's post-swap mid. Result over 10,232 swaps: median cost 25.8 bps,
5th percentile 25.01 bps, and zero swaps below 25 bps -- a hard floor exactly at
the pool's 2500 (0.25%) fee tier, violated by none. The decode is correct.

This matters because the headline numbers below look impossible at first glance:
the median swap beats Bybit's mid in BOTH directions. Against a fixed reference
the two directions would have to sum to ~2x the fee, so both cannot be
favourable. They can be here because swap timing is ENDOGENOUS -- whoever is
buying on-chain chooses moments when the pool trades below Bybit, and sellers
choose the opposite. Both sides beating the mid is the signature of selection by
informed flow, not of a broken decoder.

Event decoding notes (both verified against on-chain data):
  Agni (Pancake V3) Swap: topics = [sig, sender, recipient];
    data = amount0 int256, amount1 int256, sqrtPriceX96, liquidity, tick,
           protocolFeesToken0, protocolFeesToken1
    token0 = USDT0 (6dp), token1 = WMNT (18dp). Positive = into the pool.
  Merchant Moe LB Swap: topics = [sig, sender, to];
    data = id uint24, amountsIn bytes32, amountsOut bytes32,
           volatilityAccumulator uint24, totalFees bytes32, protocolFees bytes32
    LB packs a bytes32 as (y << 128) | x, so big-endian bytes[:16] is y = USDT0
    and bytes[16:] is x = WMNT. Confirmed empirically by decoding a
    CollectedProtocolFees payload: 478.89 / 1278.89 = 0.3745, i.e. MNT's price.
"""

from __future__ import annotations

import argparse
import json

import duckdb
import polars as pl
from eth_abi import decode as abi_decode

from .config import (BYBIT_TAKER_FEE, DATA, REPORT, USDT0_DECIMALS,
                     WMNT_DECIMALS)
from .m1_scan_events import RAW_LOGS, STATE_CHANGES
from .m3_bybit import QUOTES as BYBIT_QUOTES
from .m4_align import book_levels, round_trip_hurdle_bps, slip_fraction

SWAPS = DATA / "swaps_decoded.parquet"
ATTRIB_MD = REPORT / "attribution.md"


def _topic_addr(t: str) -> str:
    return "0x" + t[-40:].lower()


def decode_swaps() -> pl.DataFrame:
    raw = pl.read_parquet(RAW_LOGS).filter(pl.col("event_name") == "Swap")
    sc = pl.read_parquet(STATE_CHANGES).select(
        "pool_name", "block_number", "ts_ms")

    rows: list[dict] = []
    for r in raw.iter_rows(named=True):
        topics = json.loads(r["topics"])
        data = bytes.fromhex(r["data"][2:])
        sender = _topic_addr(topics[1])
        beneficiary = _topic_addr(topics[2])

        if r["event_kind"] == "v3":
            a0, a1 = abi_decode(["int256", "int256"], data[:64])
            if a0 > 0 and a1 < 0:        # USDT0 in, WMNT out -> bought MNT
                direction, usd, mnt = "A", a0 / 10 ** USDT0_DECIMALS, -a1 / 10 ** WMNT_DECIMALS
            elif a1 > 0 and a0 < 0:      # WMNT in, USDT0 out -> sold MNT
                direction, usd, mnt = "B", -a0 / 10 ** USDT0_DECIMALS, a1 / 10 ** WMNT_DECIMALS
            else:
                continue
        else:
            # id, amountsIn, amountsOut, volAcc, totalFees, protocolFees
            _id, a_in, a_out = abi_decode(
                ["uint24", "bytes32", "bytes32"], data[:96])
            in_y = int.from_bytes(a_in[:16], "big") / 10 ** USDT0_DECIMALS
            in_x = int.from_bytes(a_in[16:], "big") / 10 ** WMNT_DECIMALS
            out_y = int.from_bytes(a_out[:16], "big") / 10 ** USDT0_DECIMALS
            out_x = int.from_bytes(a_out[16:], "big") / 10 ** WMNT_DECIMALS
            if in_y > 0 and out_x > 0:   # USDT0 in, WMNT out
                direction, usd, mnt = "A", in_y, out_x
            elif in_x > 0 and out_y > 0:  # WMNT in, USDT0 out
                direction, usd, mnt = "B", out_y, in_x
            else:
                continue

        if mnt <= 0 or usd <= 0:
            continue
        rows.append({
            "pool_name": r["pool_name"], "block_number": r["block_number"],
            "tx_hash": r["tx_hash"], "log_index": r["log_index"],
            "sender": sender, "beneficiary": beneficiary,
            "direction": direction, "usd": usd, "mnt": mnt,
            "dex_price": usd / mnt,
        })

    df = pl.DataFrame(rows).join(sc, on=["pool_name", "block_number"], how="left")
    print(f"  decoded {len(df):,} swaps "
          f"({df['beneficiary'].n_unique():,} distinct beneficiaries)")
    return df


def classify_addresses(addrs: list[str]) -> dict[str, bool]:
    """True = contract. One eth_getCode per address, batched."""
    from .config import RPC_HTTP_BATCH
    from .rpc import Rpc, cs
    out: dict[str, bool] = {}
    with Rpc() as rpc:
        for i in range(0, len(addrs), RPC_HTTP_BATCH):
            part = addrs[i:i + RPC_HTTP_BATCH]
            res = rpc.batch([("eth_getCode", [cs(a), "latest"]) for a in part])
            for a, code in zip(part, res):
                out[a] = bool(code) and code != "0x"
    return out


def probe_roles(sw: pl.DataFrame, addrs: list[str]) -> dict[str, str]:
    """Is a beneficiary an entrypoint users call, or an internal contract?

    One sample transaction per address -- bounded, deliberately not per-swap.
    If tx.to == the beneficiary, users are calling it directly, so it is a
    router/aggregator and the ultimate trader behind it is not visible in the
    log. If not, the beneficiary is downstream of some other entrypoint.
    """
    from .rpc import Rpc
    sample = (sw.group_by("beneficiary").agg(pl.col("tx_hash").first())
                .filter(pl.col("beneficiary").is_in(addrs)))
    out: dict[str, str] = {}
    with Rpc() as rpc:
        rows = sample.to_dicts()
        res = rpc.batch([("eth_getTransactionByHash", [r["tx_hash"]])
                         for r in rows])
        for r, tx in zip(rows, res):
            to = (tx or {}).get("to") or ""
            out[r["beneficiary"]] = ("entrypoint"
                                     if to.lower() == r["beneficiary"]
                                     else "internal")
    return out


def realized_profit(sw: pl.DataFrame) -> pl.DataFrame:
    """What closing the other leg on Bybit at that instant would have paid."""
    bids, asks, snap_mid = book_levels()

    con = duckdb.connect()
    con.register("sw", sw.to_arrow())
    con.execute(f"CREATE VIEW byb AS SELECT * FROM read_parquet('{BYBIT_QUOTES}')")
    j = con.execute("""
        SELECT s.*, b.mid AS bybit_mid, b.timestamp AS bybit_ts_ms,
               s.ts_ms - b.timestamp AS bybit_lag_ms
        FROM sw s ASOF JOIN byb b ON s.ts_ms >= b.timestamp
    """).pl()

    # Exact VWAP walk per swap notional -- no interpolation. Memoised because
    # many swaps share a notional and the walk is O(levels).
    cache: dict[float, tuple[float | None, float | None]] = {}
    sell_slip, buy_slip = [], []
    for usd in j["usd"].to_list():
        if usd not in cache:
            cache[usd] = (slip_fraction(bids, usd, snap_mid, is_bid=True),
                          slip_fraction(asks, usd, snap_mid, is_bid=False))
        s, b = cache[usd]
        sell_slip.append(s)
        buy_slip.append(b)
    j = j.with_columns(
        pl.Series("sell_slip", sell_slip, dtype=pl.Float64),
        pl.Series("buy_slip", buy_slip, dtype=pl.Float64),
    )
    # A swap larger than the entire snapshot book has no honest close price, so
    # it gets no realized figure. Flagged and counted, never silently nulled.
    j = j.with_columns(
        (pl.col("sell_slip").is_not_null() & pl.col("buy_slip").is_not_null())
        .alias("priced")
    )
    n_unpriced = int((~j["priced"]).sum())
    if n_unpriced:
        print(f"  {n_unpriced:,} swaps exceed the snapshot book depth; "
              f"excluded from realized profit")

    # Direction A bought MNT on the DEX for `usd`; the close is selling `mnt` on
    # Bybit. Direction B sold `mnt` for `usd`; the close is buying it back.
    j = j.with_columns(
        pl.when(pl.col("direction") == "A")
          .then(pl.col("mnt") * pl.col("bybit_mid") * (1 - pl.col("sell_slip"))
                * (1 - BYBIT_TAKER_FEE) - pl.col("usd"))
          .otherwise(pl.col("usd") - pl.col("mnt") * pl.col("bybit_mid")
                     * (1 + pl.col("buy_slip")) * (1 + BYBIT_TAKER_FEE))
          .alias("realized_usd")
    ).with_columns(
        (pl.col("realized_usd") / pl.col("usd") * 1e4).alias("realized_bps"),
        (pl.col("realized_usd") > 0).alias("was_profitable"),
    )
    return j


def run(top_n: int = 10) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    sw = decode_swaps()
    sw = realized_profit(sw)

    addrs = sorted(set(sw["beneficiary"].to_list()) | set(sw["sender"].to_list()))
    print(f"  classifying {len(addrs)} addresses (contract vs EOA)...")
    is_contract = classify_addresses(addrs)
    sw = sw.with_columns(
        pl.col("beneficiary").replace_strict(is_contract, return_dtype=pl.Boolean)
          .alias("beneficiary_is_contract"),
        pl.col("sender").replace_strict(is_contract, return_dtype=pl.Boolean)
          .alias("sender_is_contract"),
    )
    # Role probe, restricted to the addresses that actually appear in the
    # report tables -- one request each, not one per swap.
    shown: set[str] = set()
    for pool in sw["pool_name"].unique():
        p = sw.filter(pl.col("pool_name") == pool)
        shown |= set(p.group_by("beneficiary").agg(pl.col("usd").sum())
                      .sort("usd", descending=True)
                      .head(top_n)["beneficiary"].to_list())
    print(f"  probing roles of {len(shown)} top beneficiaries...")
    roles = probe_roles(sw, sorted(shown))
    sw = sw.with_columns(
        pl.col("beneficiary").replace_strict(roles, default=None)
          .alias("beneficiary_role"))
    sw.write_parquet(SWAPS)

    _write_md(sw, top_n)
    print(f"\nwrote {SWAPS.name} and {ATTRIB_MD}")


_BUCKETS = [(0, 500, "< $500"), (500, 2_000, "$500 - $2K"),
            (2_000, 10_000, "$2K - $10K"), (10_000, None, "> $10K")]

# Below this many swaps on either side, a bucket's median is flagged in the
# report rather than read as a result. The top bucket has 9 Agni swaps.
THIN_N = 30


def _bucket_gaps(cost: pl.DataFrame, thin_n: int) -> list[float]:
    """Per-bucket cost gap between the two venues, thin buckets excluded.

    Excluded rather than merely flagged, because these feed a "how much of the
    hurdle does venue choice remove" range and a 9-swap median would set the
    bound.
    """
    if cost.is_empty() or cost["pool_name"].n_unique() < 2:
        return []
    pools = sorted(cost["pool_name"].unique())
    out = []
    for bkt in cost["bucket"].unique(maintain_order=True):
        vals = {r["pool_name"]: r for r in
                cost.filter(pl.col("bucket") == bkt).iter_rows(named=True)}
        if len(vals) < 2 or min(v["n"] for v in vals.values()) < thin_n:
            continue
        out.append(vals[pools[0]]["med"] - vals[pools[1]]["med"])
    return out


def _cost_by_bucket(sw: pl.DataFrame) -> pl.DataFrame:
    """Median execution cost per swap vs the pool's own PRE-swap mid, by size.

    The mid recorded at a state-change block is the state *after* that block's
    swap, so the pre-swap mid is the previous state-change block's mid. Using the
    same-block mid instead understates cost for large swaps -- it measures against
    a price the swap itself already moved -- which is enough to make Agni's fee
    floor appear to vanish above $10K. Hence the shift.
    """
    from .m2_quotes import DEX_QUOTES
    if not DEX_QUOTES.exists():
        return pl.DataFrame()
    q = (pl.read_parquet(DEX_QUOTES)
           .select("pool_name", "block_number", "mid_price").unique()
           .sort("pool_name", "block_number"))
    q = q.with_columns(
        pl.col("mid_price").shift(1).over("pool_name").alias("pre_mid"))
    s = (sw.filter(pl.col("priced"))
           .join(q.drop("mid_price"), on=["pool_name", "block_number"], how="left")
           .filter(pl.col("pre_mid").is_not_null()))
    if s.is_empty():
        return pl.DataFrame()
    s = s.with_columns(
        pl.when(pl.col("direction") == "A")
          .then((pl.col("dex_price") / pl.col("pre_mid") - 1) * 1e4)
          .otherwise((1 - pl.col("dex_price") / pl.col("pre_mid")) * 1e4)
          .alias("cost_bps"))
    expr = pl.lit(_BUCKETS[-1][2])
    for lo, hi, label in reversed(_BUCKETS[:-1]):
        expr = pl.when(pl.col("usd") < hi).then(pl.lit(label)).otherwise(expr)
    s = s.with_columns(expr.alias("bucket"))
    out = s.group_by("pool_name", "bucket").agg(
        pl.len().alias("n"),
        pl.col("cost_bps").median().alias("med"),
    )
    # Share of each venue's own flow falling in the bucket, so a reader can see
    # which rows carry the volume rather than weighting all four equally.
    tot = s.group_by("pool_name").agg(pl.len().alias("tot"))
    out = out.join(tot, on="pool_name").with_columns(
        (pl.col("n") / pl.col("tot")).alias("share"))
    order = {label: i for i, (_, _, label) in enumerate(_BUCKETS)}
    return out.with_columns(
        pl.col("bucket").replace_strict(order, return_dtype=pl.Int32).alias("o")
    ).sort("o", "pool_name").drop("o")


def _write_md(sw: pl.DataFrame, top_n: int) -> None:
    L: list[str] = []
    A = L.append
    A("# Attribution: who traded these pools, and what did they make")
    A("")
    A("_Computed from decoded on-chain Swap events plus `eth_getCode`, not from "
      "a third-party index. Realized profit is what closing the opposite leg on "
      "Bybit at that instant would have paid, net of Bybit taker fee and "
      "exact-VWAP slippage for that swap's own notional._")
    A("")

    tot_usd = sw["usd"].sum()
    A(f"**{len(sw):,} swaps**, ${tot_usd:,.0f} total notional, "
      f"{sw['beneficiary'].n_unique():,} distinct beneficiaries.")
    A("")

    A("## Concentration")
    A("")
    for pool in sorted(sw["pool_name"].unique()):
        p = sw.filter(pl.col("pool_name") == pool)
        g = (p.group_by("beneficiary").agg(
                pl.len().alias("n"),
                pl.col("usd").sum().alias("usd"),
                pl.col("realized_usd").sum().alias("realized_usd"),
                pl.col("beneficiary_is_contract").first().alias("is_contract"),
                pl.col("beneficiary_role").first().alias("role"))
              .sort("usd", descending=True))
        share = (g["usd"] / p["usd"].sum())
        hhi = float((share ** 2).sum())
        topn = float(share.head(top_n).sum())
        A(f"### {pool}")
        A("")
        A(f"{len(p):,} swaps, ${p['usd'].sum():,.0f} notional, "
          f"{len(g):,} beneficiaries. "
          f"Top-{top_n} share **{topn:.1%}**, HHI **{hhi:.3f}** "
          f"({'highly concentrated' if hhi > 0.25 else 'moderately concentrated' if hhi > 0.15 else 'dispersed'}).")
        A("")
        A("| # | beneficiary | contract | role | swaps | notional $ | "
          "realized $ | bps |")
        A("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(g.head(top_n).iter_rows(named=True), 1):
            bps = r["realized_usd"] / r["usd"] * 1e4 if r["usd"] else 0
            A(f"| {i} | `{r['beneficiary'][:10]}…{r['beneficiary'][-6:]}` | "
              f"{'yes' if r['is_contract'] else 'no'} | {r['role'] or '-'} | "
              f"{r['n']:,} | ${r['usd']:,.0f} | ${r['realized_usd']:,.0f} | "
              f"{bps:.1f} |")
        A("")

    A("**Read the concentration numbers with this caveat:** the beneficiary is "
      "the address the pool paid, which for a router or aggregator is the "
      "router itself, not the person behind it. Addresses marked `entrypoint` "
      "above are called directly by users (verified: `tx.to` equals the "
      "beneficiary), so the volume behind them is an unknown number of distinct "
      "traders. Concentration is therefore measured at the EXECUTOR level. For "
      "the arbitrage question that is arguably the level that matters -- the "
      "executor is the bot -- but it is an upper bound on true trader "
      "concentration, not an estimate of it.")
    A("")

    A("## Contract vs EOA")
    A("")
    g = sw.group_by("beneficiary_is_contract").agg(
        pl.len().alias("swaps"), pl.col("usd").sum().alias("usd"),
        pl.col("realized_usd").sum().alias("realized"),
        pl.col("beneficiary").n_unique().alias("addrs"))
    A("| beneficiary | addrs | swaps | notional $ | realized $ |")
    A("|---|---|---|---|---|")
    for r in g.sort("usd", descending=True).iter_rows(named=True):
        A(f"| {'contract' if r['beneficiary_is_contract'] else 'EOA'} | "
          f"{r['addrs']:,} | {r['swaps']:,} | ${r['usd']:,.0f} | "
          f"${r['realized']:,.0f} |")
    A("")

    A("## Realized profit vs Bybit")
    A("")
    pr = sw.filter(pl.col("priced"))
    if len(pr) < len(sw):
        A(f"_{len(sw) - len(pr):,} of {len(sw):,} swaps exceed the depth of the "
          f"Bybit snapshot book and have no honest close price; they are "
          f"excluded from this section only._")
        A("")
    prof = pr.filter(pl.col("was_profitable"))
    A(f"Of {len(pr):,} priced swaps, **{len(prof):,} "
      f"({len(prof) / max(len(pr), 1):.1%})** would have been profitable had "
      f"the trader simultaneously closed on Bybit.")
    A("")
    A(f"- Sum over profitable swaps: **${prof['realized_usd'].sum():,.0f}** "
      f"(median {prof['realized_bps'].median():.1f} bps)")
    A(f"- Sum over ALL priced swaps: **${pr['realized_usd'].sum():,.0f}** "
      f"(median {pr['realized_bps'].median():.1f} bps)")
    A("")
    A("The second figure is the honest one for 'is this flow arbitrage': if the "
      "population of takers were arbitrageurs, the all-swaps total would be "
      "solidly positive. A negative total means most on-chain flow is paying "
      "the spread, not harvesting it -- i.e. it is end-user demand, and the "
      "arbitrage is being done by a small subset.")
    A("")
    A("| venue | dir | swaps | notional $ | realized $ | median bps | "
      "% profitable |")
    A("|---|---|---|---|---|---|---|")
    for r in sw.group_by("pool_name", "direction").agg(
            pl.len().alias("n"), pl.col("usd").sum().alias("usd"),
            pl.col("realized_usd").sum().alias("realized"),
            pl.col("realized_bps").median().alias("med_bps"),
            pl.col("was_profitable").mean().alias("pct")
    ).sort(["pool_name", "direction"]).iter_rows(named=True):
        A(f"| {r['pool_name']} | {r['direction']} | {r['n']:,} | "
          f"${r['usd']:,.0f} | ${r['realized']:,.0f} | {r['med_bps']:.1f} | "
          f"{r['pct']:.1%} |")
    A("")

    # The venue split is the substantive finding; state it from the data.
    byv = pr.group_by("pool_name").agg(
        pl.col("realized_usd").sum().alias("realized"),
        pl.col("was_profitable").mean().alias("pct"),
        pl.col("realized_bps").median().alias("med")).sort("pool_name")
    # Calibration check. Must be a SELF-DIRECTED executor: an entrypoint's
    # volume is aggregated end-user flow, whose margin says nothing about
    # whether a professional finds this trade viable.
    big = (pr.filter(pl.col("beneficiary_role") == "internal")
             .group_by("beneficiary").agg(
                pl.col("usd").sum().alias("usd"),
                pl.col("realized_usd").sum().alias("realized"),
                pl.col("beneficiary_is_contract").first().alias("c"))
             .sort("usd", descending=True).head(1).to_dicts())
    if big:
        b0 = big[0]
        bps = b0["realized"] / b0["usd"] * 1e4
        A(f"**Calibration.** The largest self-directed executor -- excluding "
          f"routers, whose volume is aggregated end-user flow -- is "
          f"`{b0['beneficiary']}` "
          f"({'contract' if b0['c'] else 'EOA'}), which pushed "
          f"${b0['usd']:,.0f} of notional for ${b0['realized']:,.0f} net, "
          f"**{bps:+.1f} bps**.")
        A("")
        if abs(bps) < 2:
            A("A high-volume, evidently automated participant landing this "
              "close to zero is the strongest available evidence that this cost "
              "model is calibrated rather than merely plausible. Were it "
              "materially too harsh or too generous, the most active "
              "professional flow on the venue would not sit at its breakeven.")
        else:
            A(f"That is {abs(bps):.1f} bps from this model's breakeven, which "
              f"bounds how far the model can be miscalibrated for a "
              f"professional participant, but does not pin it as tightly as a "
              f"near-zero reading would.")
        A("")

    A("The split by venue is the substantive result:")
    A("")
    for r in byv.iter_rows(named=True):
        A(f"- **{r['pool_name']}**: {r['pct']:.0%} of swaps profitable, "
          f"median {r['med']:+.1f} bps, total ${r['realized']:,.0f}")
    A("")
    A("The two venues are not competing on equal terms, and the reason is "
      "structural rather than incidental. Measured on real swaps against each "
      "pool's own **pre-swap** mid, at matched notional:")
    A("")
    cost = _cost_by_bucket(sw)
    thin: list[str] = []
    if not cost.is_empty():
        pools = sorted(cost["pool_name"].unique())
        # Swap counts are carried inline rather than omitted: the buckets differ
        # in population by three orders of magnitude, and the sparsest one is the
        # row whose gap disagrees with the trend. A median printed without its n
        # invites reading nine swaps as a finding.
        A("| notional | " + " | ".join(f"{p} med cost (n)" for p in pools) +
          " | gap | share of Agni flow |")
        A("|---|" + "---|" * (len(pools) + 2))
        for bkt in cost["bucket"].unique(maintain_order=True):
            row = cost.filter(pl.col("bucket") == bkt)
            vals = {r["pool_name"]: r for r in row.iter_rows(named=True)}
            if len(vals) < 2:
                continue
            mark = "" if min(v["n"] for v in vals.values()) >= THIN_N else " †"
            if mark:
                thin.append(bkt)
            cells = " | ".join(
                f"{vals[p]['med']:.1f} bps ({vals[p]['n']:,})" for p in pools)
            gap = vals[pools[0]]["med"] - vals[pools[1]]["med"]
            shr = vals.get("agni_v3", {}).get("share", 0.0)
            A(f"| {bkt}{mark} | {cells} | {abs(gap):.1f} bps | {shr:.0%} |")
        A("")
        if thin:
            A(f"† fewer than {THIN_N} swaps on one side of the comparison. "
              "Reported for completeness; the median is not a reliable estimate "
              "at that count and the gap in that row should not be read as a "
              "trend.")
            A("")
    # Size the gap against the hurdle from the table's own numbers rather than
    # asserting a fraction: the hurdle lives in report.md, and a hardcoded ratio
    # here would go stale the moment either report is re-run on a new window.
    gaps = [abs(g) for g in _bucket_gaps(cost, THIN_N)]
    hurdle = round_trip_hurdle_bps(1_000)
    if gaps and hurdle:
        frac = (f"removes {min(gaps):.0f}-{max(gaps):.0f} bps -- "
                f"{min(gaps) / hurdle:.0%}-{max(gaps) / hurdle:.0%} of the "
                f"~{hurdle:.0f} bps round-trip hurdle at $1K")
    elif gaps:
        frac = f"removes {min(gaps):.0f}-{max(gaps):.0f} bps"
    else:
        frac = "removes a material share of the hurdle"
    A("Agni's cost has a hard floor at its 25 bps fee tier and then rises with "
      "price impact. Merchant Moe's Liquidity Book charges a flat ~18.8 bps for "
      "any swap that fits inside the active bin, with no impact at all -- so "
      "the gap does not merely exist, it *widens* with size, across every bucket "
      f"populated enough to measure. Routing to Moe {frac}, "
      "which is what lets the same dislocation clear Moe's bar and not Agni's. "
      "Any live strategy should route to Moe and treat Agni as a venue of last "
      "resort.")
    A("")
    A("**This does not contradict the cost ladder in `report.md`,** which shows "
      "the two venues within ~2 bps of each other at a fixed $1,000. That table "
      "quotes $1,000 at *every* state-change block, including moments when Moe's "
      "active bin is nearly exhausted and the order must cross bins at 25 bps "
      "apiece. Real traders do not route then. The ladder is the cost of "
      "demanding liquidity at an arbitrary instant; this table is the cost "
      "actually paid by flow that chose its moment. The difference between them "
      "is the value of that choice.")
    A("")

    A("## Temporal clustering")
    A("")
    for pool in sorted(sw["pool_name"].unique()):
        p = sw.filter(pl.col("pool_name") == pool).sort("ts_ms")
        gaps = p["ts_ms"].diff().drop_nulls() / 1000
        if gaps.is_empty():
            continue
        A(f"- **{pool}**: median gap between swaps {gaps.median():.0f}s, "
          f"p10 {gaps.quantile(0.10):.1f}s, p90 {gaps.quantile(0.90):.0f}s. "
          f"{(gaps < 4).sum() / len(gaps):.1%} of swaps land within 2 blocks "
          f"of the previous one.")
    A("")
    A(f"Bybit quote staleness at swap time: median "
      f"{sw['bybit_lag_ms'].median():.0f} ms, "
      f"p95 {sw['bybit_lag_ms'].quantile(0.95):.0f} ms.")
    A("")

    ATTRIB_MD.write_text("\n".join(L) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=10)
    run(top_n=ap.parse_args().top_n)


if __name__ == "__main__":
    main()
