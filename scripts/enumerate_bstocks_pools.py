#!/usr/bin/env python3
"""Authoritative PCS factory enumeration for Binance bStocks (WHI-790).

Re-runnable. Discovers BEP-20 contracts via Binance public capital API, then
queries PancakeSwap V3 ``getPool`` (fees 100/500/2500/10000) and V2 ``getPair``
against {USDT, USDC, WBNB}. Verifies ``token0/token1`` on-chain and ranks
best pool by USDT-prefer + balanceOf TVL.

Usage (from repo root, needs network + BSC RPC):

    uv run python scripts/enumerate_bstocks_pools.py
    uv run python scripts/enumerate_bstocks_pools.py --rpc "$BSC_RPC_URL"
    uv run python scripts/enumerate_bstocks_pools.py \\
        --out docs/references/m7-bstocks-enum-snapshot.json

Collector-scope AMM is PCS **V3 + USDT only** (see inventory ``pancake.amm``).
V2-only / WBNB-only hits are recorded in the snapshot but become dex:none in
the market YAML.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from decimal import Decimal
from pathlib import Path

from eth_utils import keccak

# Allow `uv run python scripts/...` without installing as package entrypoint.
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

from monitor.fluxion.rpc import Rpc, cs, encode_call  # noqa: E402

DEFAULT_RPC = "https://bsc-dataseed.binance.org"
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
V3_FACTORY = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
V2_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
STABLE_FACTORIES = [
    "0x25a55f9f2279A54951133D503490342b50E5cd15",
    "0x36bBb126e75351C0DfB651e39b38fe0BC436FFD2",
]
USDT = "0x55d398326f99059fF775485246999027B3197955"
USDC = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
QUOTES = {"USDT": USDT, "USDC": USDC, "WBNB": WBNB}
V3_FEES = (100, 500, 2500, 10000)
ZERO = "0x" + "0" * 40
CRYPTO_EXCL = {"BNB", "DGB", "TRB", "CKB", "SHIB", "ARB", "BB", "YB", "QNT", "QNTB"}
OLD_TOP12 = [
    "SPCXB",
    "SKHYB",
    "TSLAB",
    "SPYB",
    "NVDAB",
    "AAPLB",
    "GOOGLB",
    "MSFTB",
    "INTCB",
    "MUB",
    "SOXLB",
    "MUUB",
]

SEL_GET_POOL = "0x" + keccak(text="getPool(address,address,uint24)").hex()[:8]
SEL_GET_PAIR = "0x" + keccak(text="getPair(address,address)").hex()[:8]
SEL_TOKEN0 = "0x0dfe1681"
SEL_TOKEN1 = "0xd21220a7"
SEL_DECIMALS = "0x313ce567"
SEL_UIMULT = "0x" + keccak(text="uiMultiplier()").hex()[:8]
SEL_BAL = "0x70a08231"


def decode_addr(data: bytes) -> str:
    if not data or len(data) < 32:
        return ZERO
    return "0x" + data[-20:].hex()


def decode_uint(data: bytes) -> int:
    if not data:
        return 0
    return int.from_bytes(data, "big")


def http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "whi-790-enum/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def fetch_binance_bases() -> list[str]:
    d = http_json("https://data-api.binance.vision/api/v3/exchangeInfo")
    bases: list[str] = []
    for s in d["symbols"]:
        if s.get("status") != "TRADING" or s.get("quoteAsset") != "USDT":
            continue
        b = s["baseAsset"]
        if b in CRYPTO_EXCL:
            continue
        # bStock bases end with B (includes MUB). CRYPTO_EXCL drops crypto
        # false positives (BNB, SHIB, …). Re-check when Binance adds listings.
        if b.endswith("B"):
            bases.append(b)
    return sorted(set(bases))


def fetch_binance_contracts(bases: set[str]) -> dict[str, tuple[str, str]]:
    d = http_json(
        "https://www.binance.com/bapi/capital/v2/public/capital/getNetworkCoinAll"
    )
    out: dict[str, tuple[str, str]] = {}
    for coin in d.get("data") or []:
        c = coin.get("coin")
        if c not in bases:
            continue
        name = coin.get("name") or c
        for n in coin.get("networkList") or []:
            if str(n.get("network", "")).upper() == "BSC":
                addr = n.get("contractAddress")
                if addr:
                    out[c] = (addr.lower(), name)
                    break
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rpc", default=DEFAULT_RPC, help="BSC JSON-RPC URL")
    p.add_argument(
        "--out",
        type=Path,
        default=_REPO / "docs" / "references" / "m7-bstocks-enum-snapshot.json",
        help="Snapshot JSON output path",
    )
    args = p.parse_args()

    bases = fetch_binance_bases()
    print(f"exchangeInfo bases: {len(bases)}", flush=True)
    contracts = fetch_binance_contracts(set(bases))
    print(f"contracts: {len(contracts)}", flush=True)
    missing = [b for b in bases if b not in contracts]
    if missing:
        print(f"MISSING CONTRACTS {missing}", file=sys.stderr)
        return 1

    rpc = Rpc(args.rpc, multicall3=MULTICALL3, min_interval=0.05, timeout=45.0, retries=6)

    token_meta: dict[str, dict] = {}
    order = list(bases)
    for i in range(0, len(order), 40):
        chunk_bases = order[i : i + 40]
        chunk_calls: list[tuple[str, bytes]] = []
        for b in chunk_bases:
            addr = contracts[b][0]
            chunk_calls.append((addr, encode_call(SEL_DECIMALS)))
            chunk_calls.append((addr, encode_call(SEL_UIMULT)))
        rets = rpc.multicall(chunk_calls, allow_failure=True)
        for j, b in enumerate(chunk_bases):
            ok_d, data_d = rets[j * 2]
            ok_u, data_u = rets[j * 2 + 1]
            dec = decode_uint(data_d) if ok_d and data_d else None
            uim = None
            if ok_u and data_u and len(data_u) >= 32:
                raw = decode_uint(data_u)
                uim = str(Decimal(raw) / Decimal(10**18))
            token_meta[b] = {
                "address": contracts[b][0],
                "name": contracts[b][1],
                "decimals": dec,
                "ui_multiplier": uim,
            }
        print(f"token meta {i + len(chunk_bases)}/{len(order)}", flush=True)

    v3_hits: list[dict] = []
    v3_calls: list[tuple[str, bytes]] = []
    v3_keys: list[tuple[str, str, int]] = []
    for b in bases:
        token = cs(contracts[b][0])
        for qname, qaddr in QUOTES.items():
            q = cs(qaddr)
            for fee in V3_FEES:
                data = encode_call(
                    SEL_GET_POOL, ["address", "address", "uint24"], [token, q, fee]
                )
                v3_calls.append((V3_FACTORY, data))
                v3_keys.append((b, qname, fee))
    print(f"V3 getPool calls: {len(v3_calls)}", flush=True)
    for i in range(0, len(v3_calls), 80):
        chunk = v3_calls[i : i + 80]
        keys = v3_keys[i : i + 80]
        rets = rpc.multicall(chunk, allow_failure=True)
        for (b, qn, fee), (ok, data) in zip(keys, rets, strict=True):
            if not ok or not data:
                continue
            pool = decode_addr(data)
            if pool == ZERO or int(pool, 16) == 0:
                continue
            v3_hits.append(
                {"base": b, "quote": qn, "fee": fee, "pool": pool.lower(), "kind": "v3"}
            )
        print(
            f"V3 progress {min(i + 80, len(v3_calls))}/{len(v3_calls)} hits={len(v3_hits)}",
            flush=True,
        )

    v2_hits: list[dict] = []
    v2_calls: list[tuple[str, bytes]] = []
    v2_keys: list[tuple[str, str]] = []
    for b in bases:
        token = cs(contracts[b][0])
        for qname, qaddr in QUOTES.items():
            q = cs(qaddr)
            data = encode_call(SEL_GET_PAIR, ["address", "address"], [token, q])
            v2_calls.append((V2_FACTORY, data))
            v2_keys.append((b, qname))
    print(f"V2 getPair calls: {len(v2_calls)}", flush=True)
    rets = rpc.multicall(v2_calls, allow_failure=True)
    for (b, qn), (ok, data) in zip(v2_keys, rets, strict=True):
        if not ok or not data:
            continue
        pool = decode_addr(data)
        if pool == ZERO or int(pool, 16) == 0:
            continue
        v2_hits.append(
            {"base": b, "quote": qn, "fee": None, "pool": pool.lower(), "kind": "v2"}
        )
    print(f"V2 hits: {len(v2_hits)}", flush=True)

    all_hits = v3_hits + v2_hits
    verified: list[dict] = []
    for i in range(0, len(all_hits), 40):
        chunk = all_hits[i : i + 40]
        calls: list[tuple[str, bytes]] = []
        for h in chunk:
            calls.append((h["pool"], encode_call(SEL_TOKEN0)))
            calls.append((h["pool"], encode_call(SEL_TOKEN1)))
        rets = rpc.multicall(calls, allow_failure=True)
        for j, h in enumerate(chunk):
            ok0, d0 = rets[j * 2]
            ok1, d1 = rets[j * 2 + 1]
            if not (ok0 and ok1 and d0 and d1):
                h["verified"] = False
                h["reason"] = "token0/1 call failed"
                continue
            t0 = decode_addr(d0).lower()
            t1 = decode_addr(d1).lower()
            base = contracts[h["base"]][0].lower()
            quote = QUOTES[h["quote"]].lower()
            h["token0"] = t0
            h["token1"] = t1
            if base in {t0, t1} and quote in {t0, t1}:
                h["verified"] = True
                verified.append(h)
            else:
                h["verified"] = False
                h["reason"] = f"token mismatch t0={t0} t1={t1} want {base}+{quote}"
        print(
            f"verify {min(i + 40, len(all_hits))}/{len(all_hits)} ok={len(verified)}",
            flush=True,
        )

    for i in range(0, len(verified), 30):
        chunk = verified[i : i + 30]
        calls = []
        for h in chunk:
            pool = h["pool"]
            base = contracts[h["base"]][0]
            quote = QUOTES[h["quote"]]
            calls.append((base, encode_call(SEL_BAL, ["address"], [cs(pool)])))
            calls.append((quote, encode_call(SEL_BAL, ["address"], [cs(pool)])))
        rets = rpc.multicall(calls, allow_failure=True)
        for j, h in enumerate(chunk):
            okb, db = rets[j * 2]
            okq, dq = rets[j * 2 + 1]
            base_raw = decode_uint(db) if okb and db else 0
            quote_raw = decode_uint(dq) if okq and dq else 0
            bdec = token_meta[h["base"]]["decimals"] or 18
            base_amt = Decimal(base_raw) / Decimal(10**bdec)
            quote_amt = Decimal(quote_raw) / Decimal(10**18)
            h["base_balance"] = str(base_amt)
            h["quote_balance"] = str(quote_amt)
            if h["quote"] in ("USDT", "USDC"):
                if base_amt > 0 and quote_amt > 0:
                    mid = quote_amt / base_amt
                    tvl = float(quote_amt + base_amt * mid)
                else:
                    tvl = float(quote_amt * 2)
                unit = "USD"
            else:
                tvl = float(quote_amt)
                unit = "WBNB"
            h["est_tvl"] = tvl
            h["est_tvl_unit"] = unit
        print(f"tvl {min(i + 30, len(verified))}/{len(verified)}", flush=True)

    quote_rank = {"USDT": 0, "USDC": 1, "WBNB": 2}
    kind_rank = {"v3": 0, "v2": 1}
    by_base: dict[str, list] = {}
    for h in verified:
        by_base.setdefault(h["base"], []).append(h)

    best: dict[str, dict] = {}
    for b, hits in by_base.items():
        # Prefer USDT, then V3 over V2, then higher TVL, then lower fee.
        # kind before TVL so a dusty V3 USDT is not displaced by a fat V2.
        hits_sorted = sorted(
            hits,
            key=lambda h: (
                quote_rank[h["quote"]],
                kind_rank[h["kind"]],
                -h.get("est_tvl", 0),
                h.get("fee") or 10**9,
            ),
        )
        best[b] = hits_sorted[0]

    collector_scope = {
        b: h
        for b, h in best.items()
        if h["kind"] == "v3" and h["quote"] == "USDT"
    }

    stable_info = {}
    for fac in STABLE_FACTORIES:
        try:
            code = rpc.call("eth_getCode", [cs(fac), "latest"])
            stable_info[fac] = {
                "has_code": code not in (None, "0x", "0x0"),
                "code_len": len(code or ""),
            }
        except Exception as e:  # noqa: BLE001
            stable_info[fac] = {"error": str(e)}

    out = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": (
            "PCS V3 factory getPool (fees 100/500/2500/10000) + V2 factory getPair "
            "× {USDT,USDC,WBNB}; token0/1 verified; TVL via balanceOf both sides. "
            "Contracts from Binance public capital getNetworkCoinAll (BSC)."
        ),
        "base_count": len(bases),
        "bases": bases,
        "token_meta": token_meta,
        "v3_hits_raw": len(v3_hits),
        "v2_hits_raw": len(v2_hits),
        "verified_hits": verified,
        "unverified_hits": [h for h in all_hits if not h.get("verified")],
        "best_by_base": best,
        "overlap_count": len(best),
        "overlap_bases": sorted(best.keys()),
        "collector_scope_v3_usdt": sorted(collector_scope.keys()),
        "collector_scope_count": len(collector_scope),
        "stable_factory_probe": stable_info,
        "old_top12": OLD_TOP12,
        "delta_vs_old_top12": {
            "new": sorted(set(collector_scope) - set(OLD_TOP12)),
            "gone": sorted(set(OLD_TOP12) - set(collector_scope)),
            "out_of_collector_scope": {
                b: best[b]
                for b in sorted(set(best) - set(collector_scope))
            },
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}", flush=True)
    print(
        f"OVERLAP all-quotes={len(best)} collector_v3_usdt={len(collector_scope)}",
        flush=True,
    )
    for b in sorted(collector_scope, key=lambda x: -collector_scope[x].get("est_tvl", 0)):
        h = collector_scope[b]
        print(
            f"  {b:8} v3 USDT fee={h.get('fee')} "
            f"tvl={h.get('est_tvl'):.2f} {h['pool']}",
            flush=True,
        )
    print("NEW vs old12:", out["delta_vs_old_top12"]["new"], flush=True)
    print("GONE from old12:", out["delta_vs_old_top12"]["gone"], flush=True)
    print("NO_POOL (dex:none):", sorted(set(bases) - set(collector_scope)), flush=True)
    rpc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
