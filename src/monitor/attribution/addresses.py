"""Contract vs EOA classification via eth_getCode."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class CodeLookup(Protocol):
    """Minimal RPC surface for tests (avoid live Mantle).

    Production callers pass ``monitor.fluxion.rpc.Rpc``; attribution does not
    import the concrete client (DESIGN §4.3 — no WS/RPC client coupling).
    """

    def batch(self, calls: list[tuple[str, list[object]]]) -> list[object]: ...


def _rpc_address(addr: str) -> str:
    """Lowercase 0x-prefixed address for eth_getCode (Mantle accepts non-checksum)."""
    a = addr.strip().lower()
    if not a.startswith("0x"):
        a = "0x" + a
    return a


def is_contract_code(code: object) -> bool:
    """True when eth_getCode result is non-empty bytecode."""
    if code is None:
        return False
    if isinstance(code, str):
        c = code.strip().lower()
        return c not in ("", "0x", "0x0")
    return bool(code)


def classify_addresses(
    addrs: Sequence[str],
    rpc: CodeLookup,
    *,
    batch_size: int = 50,
) -> dict[str, bool]:
    """Map lowercased address → True if contract, False if EOA.

    Batches ``eth_getCode`` calls. Empty input returns {}.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    # Dedupe while preserving first-seen order for stable batching.
    seen: set[str] = set()
    unique: list[str] = []
    for a in addrs:
        key = a.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(key)

    out: dict[str, bool] = {}
    for i in range(0, len(unique), batch_size):
        part = unique[i : i + batch_size]
        calls: list[tuple[str, list[object]]] = [
            ("eth_getCode", [_rpc_address(a), "latest"]) for a in part
        ]
        results = rpc.batch(calls)
        if len(results) != len(part):
            raise RuntimeError(
                f"eth_getCode batch size mismatch: expected {len(part)}, "
                f"got {len(results)}"
            )
        for addr, code in zip(part, results, strict=True):
            out[addr] = is_contract_code(code)
    return out


class TxLookup(Protocol):
    """Minimal surface for entrypoint-vs-internal role probe."""

    def batch(self, calls: list[tuple[str, list[object]]]) -> list[object]: ...


def probe_roles(
    samples: Sequence[tuple[str, str]],
    rpc: TxLookup,
    *,
    batch_size: int = 50,
) -> dict[str, str]:
    """Map address → ``entrypoint`` | ``internal`` from one sample tx each.

    Phase-1 ``mba.m6_attribution.probe_roles`` heuristic: if ``tx.to`` equals the
    address, users call it directly (router/entrypoint); otherwise it is
    downstream of another entrypoint. ``samples`` is ``(address, tx_hash)``.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    # One sample per address (first wins).
    by_addr: dict[str, str] = {}
    for addr, tx_hash in samples:
        key = addr.lower()
        if key not in by_addr:
            by_addr[key] = tx_hash

    addrs = list(by_addr.keys())
    out: dict[str, str] = {}
    for i in range(0, len(addrs), batch_size):
        part = addrs[i : i + batch_size]
        calls: list[tuple[str, list[object]]] = [
            ("eth_getTransactionByHash", [by_addr[a]]) for a in part
        ]
        results = rpc.batch(calls)
        if len(results) != len(part):
            raise RuntimeError(
                f"eth_getTransactionByHash batch size mismatch: "
                f"expected {len(part)}, got {len(results)}"
            )
        for addr, tx in zip(part, results, strict=True):
            to = ""
            if isinstance(tx, dict):
                raw_to = tx.get("to") or ""
                to = str(raw_to).lower()
            out[addr] = "entrypoint" if to == addr else "internal"
    return out
