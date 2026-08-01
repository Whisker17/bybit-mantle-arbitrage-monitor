"""Contract vs EOA classification via eth_getCode."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from monitor.fluxion.rpc import Rpc, cs


class CodeLookup(Protocol):
    """Minimal RPC surface for tests (avoid live Mantle)."""

    def batch(self, calls: list[tuple[str, list[object]]]) -> list[object]: ...


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
    rpc: Rpc | CodeLookup,
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
            ("eth_getCode", [cs(a), "latest"]) for a in part
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


def merge_contract_flags(
    base: Mapping[str, bool],
    updates: Mapping[str, bool],
) -> dict[str, bool]:
    """Return a new map with ``updates`` overlaid on ``base`` (lowercased keys)."""
    out = {k.lower(): v for k, v in base.items()}
    for k, v in updates.items():
        out[k.lower()] = v
    return out
