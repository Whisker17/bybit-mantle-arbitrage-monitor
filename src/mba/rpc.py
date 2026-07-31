"""Minimal JSON-RPC client for the free public Mantle RPC.

Deliberately not web3.py: we only need eth_getLogs, eth_call and
eth_getBlockByNumber, and we want explicit control over batching, rate
limiting and resume, since the public endpoint throttles by IP.
"""

from __future__ import annotations

import itertools
import time
from typing import Any, Iterable

import httpx
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import to_checksum_address

from .config import MULTICALL3, RPC_MIN_INTERVAL, RPC_URL

_ids = itertools.count(1)


def cs(addr: str) -> str:
    """Checksum an address, tolerating any input casing.

    Bare `to_checksum_address` raises on a mixed-case address whose checksum is
    wrong, which is an easy way to hand-write a constant that blows up at
    runtime. Lowercasing first makes it unconditional.
    """
    return to_checksum_address(addr.lower())


class RpcError(RuntimeError):
    pass


# The public endpoint signals throttling *inside* the JSON-RPC payload
# (code -32016, HTTP 200), so an HTTP-only retry loop silently gives up.
RATE_LIMIT_CODES = {-32016, -32005, 429}


def _is_rate_limited(obj: Any) -> bool:
    items = obj if isinstance(obj, list) else [obj]
    for item in items:
        err = item.get("error") if isinstance(item, dict) else None
        if not err:
            continue
        if err.get("code") in RATE_LIMIT_CODES:
            return True
        if "rate limit" in str(err.get("message", "")).lower():
            return True
    return False


class Rpc:
    def __init__(self, url: str = RPC_URL, min_interval: float | None = None,
                 timeout: float = 60.0, retries: int = 6):
        self.url = url
        self.min_interval = (RPC_MIN_INTERVAL if min_interval is None
                             else min_interval)
        self.retries = retries
        self._last = 0.0
        self._client = httpx.Client(timeout=timeout, headers={
            "content-type": "application/json",
            "user-agent": "mba/0.1 (mantle-bybit-arb-poc)",
        })

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()

    def _post(self, payload: Any) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            self._throttle()
            try:
                r = self._client.post(self.url, json=payload)
                if r.status_code == 429 or r.status_code >= 500:
                    raise RpcError(f"HTTP {r.status_code}: {r.text[:200]}")
                r.raise_for_status()
                body = r.json()
                if _is_rate_limited(body):
                    # Back off *and* permanently slow down: the endpoint has
                    # told us our steady-state pace is too fast.
                    self.min_interval = min(self.min_interval * 1.5, 3.0)
                    raise RpcError("rate limited")
                return body
            except Exception as exc:  # noqa: BLE001 - retry everything transient
                last_exc = exc
                time.sleep(min(2 ** attempt * 0.75, 20.0))
        raise RpcError(f"giving up after {self.retries} attempts: {last_exc}")

    def call(self, method: str, params: list) -> Any:
        res = self._post({"jsonrpc": "2.0", "id": next(_ids),
                          "method": method, "params": params})
        if "error" in res:
            raise RpcError(f"{method} {res['error']}")
        return res["result"]

    def batch(self, calls: Iterable[tuple[str, list]]) -> list[Any]:
        """One HTTP round trip, many JSON-RPC calls. Preserves order."""
        calls = list(calls)
        if not calls:
            return []
        payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p}
                   for i, (m, p) in enumerate(calls)]
        res = self._post(payload)
        if isinstance(res, dict):  # some endpoints error the whole batch
            raise RpcError(f"batch rejected: {res}")
        by_id = {item["id"]: item for item in res}
        out = []
        for i in range(len(calls)):
            item = by_id.get(i)
            if item is None:
                raise RpcError(f"missing response for batch index {i}")
            if "error" in item:
                raise RpcError(f"{calls[i][0]} {item['error']}")
            out.append(item["result"])
        return out

    # --- convenience ----------------------------------------------------
    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def safe_head(self, lag: int | None = None) -> int:
        """A head block every node in the pool can actually serve.

        Behind a load balancer, eth_blockNumber may be ahead of the node that
        answers the following request, so the naive head 404s. Back off by
        `lag`, then walk back further until a block genuinely resolves.
        """
        from .config import HEAD_LAG_BLOCKS
        lag = HEAD_LAG_BLOCKS if lag is None else lag
        candidate = self.block_number() - lag
        for _ in range(10):
            if self.call("eth_getBlockByNumber", [hex(candidate), False]):
                return candidate
            candidate -= lag or 30
        raise RpcError(f"no settled head found near {candidate}")

    def get_logs(self, addresses: list[str], from_block: int, to_block: int,
                 topics: list | None = None) -> list[dict]:
        params = {
            "address": [cs(a) for a in addresses],
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
        }
        if topics:
            params["topics"] = topics
        return self.call("eth_getLogs", [params])

    def block_timestamps(self, blocks: list[int], chunk: int | None = None) -> dict[int, int]:
        """Fetch real timestamps rather than extrapolating from 2s blocks."""
        from .config import RPC_HTTP_BATCH
        chunk = chunk or RPC_HTTP_BATCH
        out: dict[int, int] = {}
        for i in range(0, len(blocks), chunk):
            part = blocks[i:i + chunk]
            res = self.batch([("eth_getBlockByNumber", [hex(b), False])
                              for b in part])
            for b, blk in zip(part, res):
                if blk is None:
                    raise RpcError(f"block {b} not found")
                out[b] = int(blk["timestamp"], 16)
        return out


# --- Multicall3 ---------------------------------------------------------
# aggregate3((address target, bool allowFailure, bytes callData)[])
#   -> (bool success, bytes returnData)[]
AGGREGATE3_SELECTOR = "0x82ad56cb"


def encode_aggregate3(calls: list[tuple[str, bytes]], allow_failure: bool = True) -> str:
    payload = abi_encode(
        ["(address,bool,bytes)[]"],
        [[(cs(t), allow_failure, d) for t, d in calls]],
    )
    return AGGREGATE3_SELECTOR + payload.hex()


def decode_aggregate3(hexdata: str) -> list[tuple[bool, bytes]]:
    raw = bytes.fromhex(hexdata[2:] if hexdata.startswith("0x") else hexdata)
    (results,) = abi_decode(["(bool,bytes)[]"], raw)
    return [(ok, bytes(ret)) for ok, ret in results]


def multicall(rpc: Rpc, calls: list[tuple[str, bytes]], block: int | str = "latest",
              allow_failure: bool = True) -> list[tuple[bool, bytes]]:
    tag = block if isinstance(block, str) else hex(block)
    data = encode_aggregate3(calls, allow_failure)
    ret = rpc.call("eth_call", [{"to": MULTICALL3, "data": data}, tag])
    return decode_aggregate3(ret)


def encode_call(selector: str, arg_types: list[str] | None = None,
                args: list | None = None) -> bytes:
    out = bytes.fromhex(selector[2:])
    if arg_types:
        out += abi_encode(arg_types, args or [])
    return out
