"""Minimal Mantle JSON-RPC client (adapted from mba.rpc for live collectors)."""

from __future__ import annotations

import itertools
import time
from typing import Any

import httpx
from eth_abi import decode as abi_decode  # type: ignore[attr-defined]
from eth_abi import encode as abi_encode  # type: ignore[attr-defined]
from eth_utils import to_checksum_address  # type: ignore[attr-defined]

from monitor.fluxion.http_errors import is_non_retryable_client_error

RATE_LIMIT_CODES = {-32016, -32005, 429}
AGGREGATE3_SELECTOR = "0x82ad56cb"
_ids = itertools.count(1)


def cs(addr: str) -> str:
    """Checksum address; lowercase first so mixed-case constants never blow up."""
    return to_checksum_address(addr.lower())


class RpcError(RuntimeError):
    pass


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
    def __init__(
        self,
        url: str,
        *,
        multicall3: str = "0xca11bde05977b3631167028862be2a173976ca11",
        min_interval: float = 0.05,
        timeout: float = 30.0,
        retries: int = 5,
    ) -> None:
        self.url = url
        self.multicall3 = multicall3
        self.min_interval = min_interval
        self.retries = retries
        self._last = 0.0
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "content-type": "application/json",
                "user-agent": "monitor/0.1 (bybit-mantle-arbitrage-monitor)",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Rpc:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()

    def _post(self, payload: Any) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            # Closed client never recovers — do not burn retries / backoff
            # (WHI-835: zombie shutdown spun hours on "client has been closed").
            if self._client.is_closed:
                raise RpcError(
                    "httpx client has been closed; refusing to post"
                )
            self._throttle()
            try:
                r = self._client.post(self.url, json=payload)
                if r.status_code == 429 or r.status_code >= 500:
                    raise RpcError(f"HTTP {r.status_code}: {r.text[:200]}")
                r.raise_for_status()
                body = r.json()
                if _is_rate_limited(body):
                    self.min_interval = min(self.min_interval * 1.5, 3.0)
                    raise RpcError("rate limited")
                return body
            except Exception as exc:  # noqa: BLE001 - retry transient faults
                last_exc = exc
                if is_non_retryable_client_error(exc):
                    break
                time.sleep(min(2**attempt * 0.5, 15.0))
        raise RpcError(f"giving up after {self.retries} attempts: {last_exc}")

    def call(self, method: str, params: list[Any]) -> Any:
        res = self._post(
            {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params}
        )
        if "error" in res:
            raise RpcError(f"{method} {res['error']}")
        return res["result"]

    def batch(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        if not calls:
            return []
        payload = [
            {"jsonrpc": "2.0", "id": i, "method": m, "params": p}
            for i, (m, p) in enumerate(calls)
        ]
        res = self._post(payload)
        if isinstance(res, dict):
            raise RpcError(f"batch rejected: {res}")
        by_id = {item["id"]: item for item in res}
        out: list[Any] = []
        for i in range(len(calls)):
            item = by_id.get(i)
            if item is None:
                raise RpcError(f"missing response for batch index {i}")
            if "error" in item:
                raise RpcError(f"{calls[i][0]} {item['error']}")
            out.append(item["result"])
        return out

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def get_block(self, number: int, full_txs: bool = False) -> dict[str, Any] | None:
        result = self.call("eth_getBlockByNumber", [hex(number), full_txs])
        return result if result is None or isinstance(result, dict) else None

    def get_logs(
        self,
        addresses: list[str],
        from_block: int,
        to_block: int,
        topics: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "address": [cs(a) for a in addresses],
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
        }
        if topics:
            params["topics"] = topics
        result = self.call("eth_getLogs", [params])
        if not isinstance(result, list):
            raise RpcError(f"eth_getLogs returned non-list: {type(result)}")
        return result

    def get_transaction_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        result = self.call("eth_getTransactionReceipt", [tx_hash])
        return result if result is None or isinstance(result, dict) else None

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        tag = block if isinstance(block, str) else hex(block)
        result = self.call("eth_call", [{"to": cs(to), "data": data}, tag])
        if not isinstance(result, str):
            raise RpcError(f"eth_call returned non-str: {type(result)}")
        return result

    def multicall(
        self,
        calls: list[tuple[str, bytes]],
        block: int | str = "latest",
        *,
        allow_failure: bool = True,
    ) -> list[tuple[bool, bytes]]:
        tag = block if isinstance(block, str) else hex(block)
        data = encode_aggregate3(calls, allow_failure)
        ret = self.call("eth_call", [{"to": cs(self.multicall3), "data": data}, tag])
        return decode_aggregate3(ret)


def encode_aggregate3(
    calls: list[tuple[str, bytes]], allow_failure: bool = True
) -> str:
    payload = abi_encode(
        ["(address,bool,bytes)[]"],
        [[(cs(t), allow_failure, d) for t, d in calls]],
    )
    return AGGREGATE3_SELECTOR + payload.hex()


def decode_aggregate3(hexdata: str) -> list[tuple[bool, bytes]]:
    raw = bytes.fromhex(hexdata[2:] if hexdata.startswith("0x") else hexdata)
    (results,) = abi_decode(["(bool,bytes)[]"], raw)
    return [(ok, bytes(ret)) for ok, ret in results]


def encode_call(
    selector: str, arg_types: list[str] | None = None, args: list[Any] | None = None
) -> bytes:
    out = bytes.fromhex(selector[2:] if selector.startswith("0x") else selector)
    if arg_types:
        out += abi_encode(arg_types, args or [])
    return out
