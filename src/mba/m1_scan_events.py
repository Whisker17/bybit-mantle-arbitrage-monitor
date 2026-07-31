"""M1 -- find every block in the last 29 days where either pool's price state
changed.

Why this exists: pool state is piecewise-constant between Swap/Mint/Burn, so
quoting at state-change blocks yields an *exact* step-function quote curve.
That is ~2k RPC calls instead of ~1.25M for per-block polling -- and it is not
an approximation.

Why we do NOT stop here and use these events as the sample: the signal we are
measuring is "a spread existed and nobody took it", which by definition happens
in blocks with NO trade. Measured over the real 29-day window: 16,688 distinct
price-moving blocks, i.e. 575/day out of 43,200 = 1.3% of blocks, and precisely
the 1.3% where somebody did trade. Treating those as a sample would invert the
conclusion. M2 turns this block list back into a continuous-in-time step curve.

Measured composition of the window (29.0 days, 42,756 logs):
  agni_v3  10,233 Swap + 1 CollectProtocol, and ZERO Mint/Burn -- the Agni
           pool's liquidity was completely static, so its state moves only on
           swaps. This is a hard fact, not an unmatched signature: the fetch is
           address-filtered only, and no agni log fell into "unknown".
  moe_lb    3,547 Swap, 5,794 DepositedToBins, 5,793 WithdrawnFromBins

Usage:
    python -m mba.m1_scan_events            # full 29-day scan, resumable
    python -m mba.m1_scan_events --days 2   # quick smoke test
"""

from __future__ import annotations

import argparse
import json
import time

import polars as pl
from eth_utils import keccak

from .config import BLOCKS_PER_DAY, DATA, GETLOGS_MAX_SPAN, POOLS, BACKFILL_DAYS
from .rpc import Rpc, cs

# Event signatures -> topic0. We do not filter by topic on the RPC side (these
# pools are quiet enough that fetching all their logs is cheap, and an
# unfiltered fetch cannot silently miss an event we mislabelled). Topics are
# only used to classify afterwards.
EVENT_SIGS = {
    # Agni V3. NOTE: Agni is a *PancakeSwap* V3 fork, not a vanilla Uniswap V3
    # one -- its Swap event carries two extra protocolFees args, so the vanilla
    # Uniswap topic0 matches nothing here. Both are registered; the unknown-
    # topic0 report below is what caught this.
    "Swap(address,address,int256,int256,uint160,uint128,int24,uint128,uint128)": ("v3", "Swap"),
    "Swap(address,address,int256,int256,uint160,uint128,int24)": ("v3", "Swap"),
    "Mint(address,address,int24,int24,uint128,uint256,uint256)": ("v3", "Mint"),
    "Burn(address,int24,int24,uint128,uint256,uint256)": ("v3", "Burn"),
    "Collect(address,address,int24,int24,uint128,uint128)": ("v3", "Collect"),
    "CollectProtocol(address,address,uint128,uint128)": ("v3", "CollectProtocol"),
    "Flash(address,address,uint256,uint256,uint256,uint256)": ("v3", "Flash"),
    "SetFeeProtocol(uint8,uint8,uint8,uint8)": ("v3", "SetFeeProtocol"),
    "IncreaseObservationCardinalityNext(uint16,uint16)": ("v3", "IncreaseObservationCardinalityNext"),
    # Liquidity Book v2.2 (Merchant Moe)
    "Swap(address,address,uint24,bytes32,bytes32,uint24,bytes32,bytes32)": ("lb", "Swap"),
    "DepositedToBins(address,address,uint256[],bytes32[])": ("lb", "DepositedToBins"),
    "WithdrawnFromBins(address,address,uint256[],bytes32[])": ("lb", "WithdrawnFromBins"),
    "CompositionFees(address,uint24,bytes32,bytes32)": ("lb", "CompositionFees"),
    "ForwardedToBin(uint24,bytes32,bytes32)": ("lb", "ForwardedToBin"),
    "FlashLoan(address,address,uint24,bytes32,bytes32,bytes32)": ("lb", "FlashLoan"),
    "StaticFeeParametersSet(address,uint16,uint16,uint16,uint16,uint24,uint16,uint24)": ("lb", "StaticFeeParametersSet"),
    # LBToken LP shares are ERC1155-style; these accompany deposits/withdrawals
    # but carry no price information of their own.
    "TransferBatch(address,address,address,uint256[],uint256[])": ("lb", "TransferBatch"),
    "TransferSingle(address,address,address,uint256,uint256)": ("lb", "TransferSingle"),
    "ApprovalForAll(address,address,bool)": ("lb", "ApprovalForAll"),
    # 7 sweeps in the 29-day window. LB v2.2 accrues protocol fees in a slot
    # separate from bin liquidity, so collecting them does not move the active
    # bin -- deliberately NOT in PRICE_MOVING. Verified by decoding the packed
    # bytes32: 478.89 USDT0 + 1278.89 MNT, ratio 0.3745, i.e. MNT's price then.
    "CollectedProtocolFees(address,bytes32)": ("lb", "CollectedProtocolFees"),
}
TOPIC0 = {"0x" + keccak(text=sig).hex(): meta for sig, meta in EVENT_SIGS.items()}

# Only these actually move the executable price. Collect/Flash/CompositionFees
# touch fees or balances, not sqrtPriceX96 / activeId / bin liquidity.
PRICE_MOVING = {"Swap", "Mint", "Burn", "DepositedToBins", "WithdrawnFromBins"}

CHECKPOINT = DATA / "m1_checkpoint.json"
RAW_LOGS = DATA / "raw_logs.parquet"
STATE_CHANGES = DATA / "state_changes.parquet"


def scan(days: int = BACKFILL_DAYS, resume: bool = True) -> pl.DataFrame:
    DATA.mkdir(parents=True, exist_ok=True)
    addresses = [cs(a) for a in POOLS]

    with Rpc() as rpc:
        head = rpc.safe_head()
        start = head - days * BLOCKS_PER_DAY
        head_ts = rpc.block_timestamps([head])[head]

        cursor = start
        rows: list[dict] = []
        if resume and CHECKPOINT.exists() and RAW_LOGS.exists():
            ck = json.loads(CHECKPOINT.read_text())
            if ck.get("start") == start:
                cursor = ck["cursor"]
                rows = pl.read_parquet(RAW_LOGS).to_dicts()
                print(f"resuming at block {cursor} with {len(rows)} logs")

        pages = -(-(head - cursor + 1) // GETLOGS_MAX_SPAN)
        print(f"head={head} start={start} span={head - start} "
              f"({days}d) pages_left={pages}")

        page = 0
        t0 = time.monotonic()
        while cursor <= head:
            to_blk = min(cursor + GETLOGS_MAX_SPAN - 1, head)
            logs = rpc.get_logs(addresses, cursor, to_blk)
            for lg in logs:
                topic0 = lg["topics"][0].lower()
                kind, name = TOPIC0.get(topic0, ("?", "unknown"))
                addr = lg["address"].lower()
                rows.append({
                    "pool": addr,
                    "pool_name": POOLS[addr]["name"],
                    "block_number": int(lg["blockNumber"], 16),
                    "tx_hash": lg["transactionHash"],
                    "log_index": int(lg["logIndex"], 16),
                    "topic0": topic0,
                    "event_kind": kind,
                    "event_name": name,
                    "topics": json.dumps(lg["topics"]),
                    "data": lg["data"],
                })
            page += 1
            cursor = to_blk + 1
            if page % 10 == 0 or cursor > head:
                pl.DataFrame(rows).write_parquet(RAW_LOGS)
                CHECKPOINT.write_text(json.dumps(
                    {"start": start, "cursor": cursor, "head": head,
                     "head_ts": head_ts}))
                rate = page / max(time.monotonic() - t0, 1e-9)
                print(f"  page {page}/{pages} block {cursor} "
                      f"logs={len(rows)} {rate:.1f} pages/s")

        raw = pl.DataFrame(rows)
        raw.write_parquet(RAW_LOGS)

        # An unrecognised topic0 is not cosmetic: if it turns out to be a Swap
        # variant we did not register, every quote downstream is taken at a
        # stale block. Print full hashes (untruncated) so they can be resolved.
        unknown = raw.filter(pl.col("event_name") == "unknown")
        if len(unknown):
            print(f"\n!! {len(unknown)} logs with unrecognised topic0 -- resolve "
                  "these before trusting the state-change list:")
            for row in unknown.group_by("pool_name", "topic0").len().sort(
                    "len", descending=True).iter_rows(named=True):
                print(f"   {row['pool_name']:8} {row['topic0']} x{row['len']}")

        # Sanity check: every pool must show swaps, or our topic set is wrong.
        for pool_name in {p["name"] for p in POOLS.values()}:
            n = len(raw.filter((pl.col("pool_name") == pool_name)
                               & (pl.col("event_name") == "Swap")))
            if n == 0:
                raise SystemExit(
                    f"ABORT: zero Swap events matched for {pool_name}. The "
                    "event signature is almost certainly a fork variant -- "
                    "resolve the unknown topic0 above before continuing.")

        # One row per (pool, block) that actually moved price.
        sc = (raw.filter(pl.col("event_name").is_in(PRICE_MOVING))
                 .group_by("pool", "pool_name", "block_number")
                 .agg(pl.col("event_name").unique().sort().str.join(",")
                        .alias("events"),
                      pl.len().alias("n_events"))
                 .sort("pool_name", "block_number"))

        blocks = sorted(sc["block_number"].unique().to_list())
        print(f"\nfetching timestamps for {len(blocks)} distinct blocks...")
        ts = rpc.block_timestamps(blocks)
        sc = sc.with_columns(
            pl.col("block_number").replace_strict(ts).alias("block_timestamp")
        ).with_columns(
            (pl.col("block_timestamp") * 1000).alias("ts_ms")
        )
        sc.write_parquet(STATE_CHANGES)

    print(f"\nwrote {STATE_CHANGES} -- {len(sc)} state-change points, "
          f"{len(blocks)} distinct blocks")
    print(raw.group_by("pool_name", "event_name").len()
             .sort(["pool_name", "len"], descending=[False, True]))
    return sc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=BACKFILL_DAYS)
    ap.add_argument("--no-resume", action="store_true")
    a = ap.parse_args()
    scan(days=a.days, resume=not a.no_resume)


if __name__ == "__main__":
    main()
