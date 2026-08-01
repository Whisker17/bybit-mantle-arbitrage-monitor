"""Contract vs EOA classification via eth_getCode; entrypoint role probe."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from monitor.attribution.config import AttributionConfig

if TYPE_CHECKING:
    from monitor.attribution.events import AmmTradeEvent


class BatchRpc(Protocol):
    """Minimal JSON-RPC batch surface (eth_getCode / eth_getTransactionByHash).

    Production callers pass ``monitor.fluxion.rpc.Rpc``; attribution does not
    import the concrete client (DESIGN §4.3 — no WS/RPC client coupling).
    """

    def batch(self, calls: list[tuple[str, list[object]]]) -> list[object]: ...


class AddressRole(StrEnum):
    """Entrypoint vs internal (phase-1 m6 ``probe_roles``)."""

    ENTRYPOINT = "entrypoint"
    INTERNAL = "internal"


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
    rpc: BatchRpc,
    *,
    batch_size: int,
) -> dict[str, bool]:
    """Map lowercased address → True if contract, False if EOA.

    Batches ``eth_getCode`` calls. Empty input returns {}.
    Pass ``batch_size=config.address_code_batch_size`` (no silent default).
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


def classify_addresses_from_config(
    addrs: Sequence[str],
    rpc: BatchRpc,
    config: AttributionConfig,
) -> dict[str, bool]:
    """``classify_addresses`` with ``config.address_code_batch_size``."""
    return classify_addresses(
        addrs, rpc, batch_size=config.address_code_batch_size
    )


def probe_roles(
    samples: Sequence[tuple[str, str]],
    rpc: BatchRpc,
    *,
    batch_size: int,
) -> dict[str, AddressRole]:
    """Map address → entrypoint | internal from one sample tx each.

    Phase-1 ``mba.m6_attribution.probe_roles`` heuristic: if ``tx.to`` equals the
    address, users call it directly (router/entrypoint); otherwise it is
    downstream of another entrypoint. ``samples`` is ``(address, tx_hash)``.
    Pass ``batch_size=config.address_code_batch_size``.
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
    out: dict[str, AddressRole] = {}
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
            # RPC miss / null receipt → skip (leave role unknown), not a false
            # "internal" label from empty to-address.
            if not isinstance(tx, dict):
                continue
            raw_to = tx.get("to") or ""
            to = str(raw_to).lower()
            if not to:
                continue
            out[addr] = (
                AddressRole.ENTRYPOINT if to == addr else AddressRole.INTERNAL
            )
    return out


def probe_roles_from_config(
    samples: Sequence[tuple[str, str]],
    rpc: BatchRpc,
    config: AttributionConfig,
) -> dict[str, AddressRole]:
    """``probe_roles`` with ``config.address_code_batch_size``."""
    return probe_roles(samples, rpc, batch_size=config.address_code_batch_size)


def role_samples_from_trades(
    trades: Sequence[AmmTradeEvent],
) -> list[tuple[str, str]]:
    """Build ``(taker, tx_hash)`` samples for ``probe_roles`` (first tx per taker).

    Uses the taker (recipient) so router entrypoints are detected when
    ``tx.to == taker``.
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for t in trades:
        addr = t.taker.lower()
        if addr in seen:
            continue
        seen.add(addr)
        out.append((addr, t.tx_hash))
    return out
