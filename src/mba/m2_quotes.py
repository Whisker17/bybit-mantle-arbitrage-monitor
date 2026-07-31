"""M2 -- historical batched quoting at every state-change block.

Two passes over each pool's own state-change blocks (from M1):
  pass 1  read pool state -> mid price, and the block's baseFeePerGas
  pass 2  quote 6 notional sizes x 2 directions against the real contracts

Pass 1 exists because direction B (sell MNT on-chain) needs a notional
expressed in MNT, which needs a price. Using the pool's own mid at that block
keeps the whole step function self-contained -- no dependency on M3, and the
pool-mid-vs-Bybit-mid comparison is itself part of the analysis.

The output is an exact step function, not a sample: pool state does not change
between these blocks, so the quote between block N and block N+1 is literally
the pass-2 quote at block N.

Scale and resumability
----------------------
The real window is 17,685 (pool, block) pairs -> ~212k quote subcalls. Each pass
is checkpointed to its own parquet after every flush and resumes by skipping
blocks already present, because an hour-long job that loses everything on a
transient RPC error is not an acceptable design. Pass 1 packs its multicall and
its header fetch into a SINGLE HTTP batch, halving that pass's round trips at no
cost in fidelity.
"""

from __future__ import annotations

import argparse
import json
import time

import polars as pl

from .config import (BACKFILL_DAYS, BLOCKS_PER_DAY, DATA, MULTICALL3, POOLS,
                     RPC_HTTP_BATCH, SIZE_LADDER_USD)
from .m1_scan_events import STATE_CHANGES
from .rpc import Rpc, cs, decode_aggregate3, encode_aggregate3
from .venues import (decimals_of, decode_quote, decode_state, quote_call,
                     state_calls, token_in_for)

DEX_STATE = DATA / "dex_state.parquet"
DEX_QUOTES = DATA / "dex_quotes.parquet"
CHECKPOINT = DATA / "m2_checkpoint.json"

DIRECTIONS = ["A", "B"]  # A = USDT0 -> WMNT (buy MNT), B = WMNT -> USDT0

FLUSH_EVERY = 40  # HTTP batches between checkpoint writes


def _part(name: str, which: str):
    return DATA / f"m2_{which}_{name}.parquet"


def _load_part(name: str, which: str) -> pl.DataFrame | None:
    p = _part(name, which)
    if p.exists():
        try:
            return pl.read_parquet(p)
        except Exception:  # noqa: BLE001 - a torn write just means start over
            return None
    return None


def pass1_state(rpc: Rpc, pool: str, blocks: list[int]) -> pl.DataFrame:
    """Pool state + base fee at each block. Resumable."""
    meta = POOLS[pool]
    kind, name = meta["kind"], meta["name"]
    bin_step = meta.get("bin_step", 0)

    done = _load_part(name, "state")
    rows: list[dict] = done.to_dicts() if done is not None else []
    have = {r["block_number"] for r in rows}
    todo = [b for b in blocks if b not in have]
    if have:
        print(f"    resuming state: {len(have)} done, {len(todo)} to go")

    t0 = time.monotonic()
    for i in range(0, len(todo), RPC_HTTP_BATCH):
        part = todo[i:i + RPC_HTTP_BATCH]
        # One HTTP round trip carries both the multicalls and the headers.
        calls: list[tuple[str, list]] = []
        for b in part:
            calls.append(("eth_call", [
                {"to": cs(MULTICALL3),
                 "data": encode_aggregate3(state_calls(cs(pool), kind))}, hex(b)]))
        for b in part:
            calls.append(("eth_getBlockByNumber", [hex(b), False]))
        res = rpc.batch(calls)
        rets, hdrs = res[:len(part)], res[len(part):]

        for b, ret, hdr in zip(part, rets, hdrs):
            st = decode_state(kind, decode_aggregate3(ret), bin_step)
            if not st or hdr is None:
                continue
            rows.append({
                "pool": pool, "pool_name": name, "block_number": b,
                "block_timestamp": int(hdr["timestamp"], 16),
                "base_fee_wei": int(hdr.get("baseFeePerGas") or "0x0", 16),
                **st,
            })

        batch_i = i // RPC_HTTP_BATCH
        if batch_i % FLUSH_EVERY == 0:
            pl.DataFrame(rows).write_parquet(_part(name, "state"))
        if batch_i % 25 == 0:
            done_n = i + len(part)
            rate = done_n / max(time.monotonic() - t0, 1e-9)
            eta = (len(todo) - done_n) / max(rate, 1e-9) / 60
            print(f"    state {done_n}/{len(todo)} ({rate:.0f} blk/s, "
                  f"eta {eta:.0f}m)")

    out = pl.DataFrame(rows)
    out.write_parquet(_part(name, "state"))
    return out


def pass2_quotes(rpc: Rpc, pool: str, state: pl.DataFrame) -> pl.DataFrame:
    """6 sizes x 2 directions per block, via one aggregate3 per block. Resumable."""
    meta = POOLS[pool]
    kind, name = meta["kind"], meta["name"]

    done = _load_part(name, "quotes")
    rows: list[dict] = done.to_dicts() if done is not None else []
    have = {r["block_number"] for r in rows}
    recs = [r for r in state.sort("block_number").to_dicts()
            if r["block_number"] not in have]
    if have:
        print(f"    resuming quotes: {len(have)} blocks done, {len(recs)} to go")

    t0 = time.monotonic()
    for i in range(0, len(recs), RPC_HTTP_BATCH):
        part = recs[i:i + RPC_HTTP_BATCH]
        layout: dict[int, list[tuple[str, int, int]]] = {}
        calls: list[tuple[str, list]] = []
        for rec in part:
            sub, lay = [], []
            for direction in DIRECTIONS:
                token_in = token_in_for(direction)
                dec = decimals_of(token_in)
                for size in SIZE_LADDER_USD:
                    # A: notional is USD == USDT0 1:1. B: convert via the
                    # pool's own mid at this block.
                    units = size if direction == "A" else size / rec["mid_price"]
                    amount_in = int(units * 10 ** dec)
                    sub.append(quote_call(cs(pool), kind, token_in, amount_in))
                    lay.append((direction, size, amount_in))
            layout[rec["block_number"]] = lay
            calls.append(("eth_call", [
                {"to": cs(MULTICALL3), "data": encode_aggregate3(sub)},
                hex(rec["block_number"])]))

        res = rpc.batch(calls)
        for rec, ret in zip(part, res):
            b = rec["block_number"]
            got = decode_aggregate3(ret)
            for (direction, size, amount_in), (ok, data) in zip(layout[b], got):
                q = decode_quote(kind, ok, data)
                rows.append({
                    "pool": pool, "pool_name": name, "block_number": b,
                    "block_timestamp": rec["block_timestamp"],
                    "mid_price": rec["mid_price"],
                    "base_fee_wei": rec["base_fee_wei"],
                    "direction": direction, "size_usd": size,
                    "amount_in": amount_in, "amount_out": q.amount_out,
                    "amount_in_left": q.amount_in_left,
                    "gas_estimate": q.gas_estimate, "quote_ok": q.ok,
                })

        batch_i = i // RPC_HTTP_BATCH
        if batch_i % FLUSH_EVERY == 0:
            pl.DataFrame(rows).write_parquet(_part(name, "quotes"))
        if batch_i % 25 == 0:
            done_n = i + len(part)
            rate = done_n / max(time.monotonic() - t0, 1e-9)
            eta = (len(recs) - done_n) / max(rate, 1e-9) / 60
            print(f"    quotes {done_n}/{len(recs)} ({rate:.0f} blk/s, "
                  f"eta {eta:.0f}m)")

    out = pl.DataFrame(rows)
    out.write_parquet(_part(name, "quotes"))
    return out


def run(days: int = BACKFILL_DAYS, limit: int | None = None) -> None:
    sc = pl.read_parquet(STATE_CHANGES)
    all_state, all_quotes = [], []

    with Rpc() as rpc:
        head = rpc.safe_head()
        window_start = head - days * BLOCKS_PER_DAY

        for pool in POOLS:
            name = POOLS[pool]["name"]
            blocks = sorted(sc.filter(pl.col("pool") == pool)["block_number"]
                              .unique().to_list())
            # Anchor the step function at the window start, otherwise the
            # series is undefined until this pool's first trade.
            if blocks and blocks[0] > window_start:
                blocks = [window_start] + blocks
            if limit:
                blocks = blocks[:limit]
            print(f"  {name}: {len(blocks)} state-change blocks")

            st = pass1_state(rpc, pool, blocks)
            print(f"  {name}: state ok for {len(st)}/{len(blocks)} blocks")
            qt = pass2_quotes(rpc, pool, st)
            bad = qt.filter(~pl.col("quote_ok"))
            partial = qt.filter(pl.col("amount_in_left") > 0)
            print(f"  {name}: {len(qt)} quotes, {len(bad)} failed, "
                  f"{len(partial)} partial-fill")
            all_state.append(st)
            all_quotes.append(qt)

    pl.concat(all_state, how="diagonal").write_parquet(DEX_STATE)
    q = pl.concat(all_quotes, how="diagonal")
    q.write_parquet(DEX_QUOTES)
    CHECKPOINT.write_text(json.dumps({"days": days, "head": head}))
    print(f"\nwrote {DEX_STATE} and {DEX_QUOTES} ({len(q)} rows)")

    # Effective one-way cost vs mid, per venue and size -- this is the number
    # that actually sets the arbitrage floor, and it is *not* just the pool fee.
    eff = q.filter(pl.col("quote_ok") & (pl.col("amount_in_left") == 0)).with_columns(
        pl.when(pl.col("direction") == "A")
          .then(pl.col("amount_in") / 1e6 / (pl.col("amount_out") / 1e18))
          .otherwise((pl.col("amount_out") / 1e6) / (pl.col("amount_in") / 1e18))
          .alias("eff_price")
    ).with_columns(
        pl.when(pl.col("direction") == "A")
          .then((pl.col("eff_price") / pl.col("mid_price") - 1) * 1e4)
          .otherwise((1 - pl.col("eff_price") / pl.col("mid_price")) * 1e4)
          .alias("cost_bps")
    )
    pl.Config.set_tbl_rows(30)
    print("\none-way DEX cost vs mid (bps), median by venue/direction/size:")
    print(eff.group_by("pool_name", "size_usd").agg(
        pl.col("cost_bps").filter(pl.col("direction") == "A").median().round(1)
          .alias("A_bps"),
        pl.col("cost_bps").filter(pl.col("direction") == "B").median().round(1)
          .alias("B_bps"),
    ).sort(["pool_name", "size_usd"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=BACKFILL_DAYS)
    ap.add_argument("--limit", type=int, default=None,
                    help="cap blocks per pool (smoke test)")
    a = ap.parse_args()
    run(days=a.days, limit=a.limit)


if __name__ == "__main__":
    main()
