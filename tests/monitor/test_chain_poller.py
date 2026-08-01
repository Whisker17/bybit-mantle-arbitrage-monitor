"""Seams: ChainPoller advances head, gap on long lag, no historical backfill."""

from __future__ import annotations

from typing import Any

from monitor.fluxion.chain import ChainPoller
from monitor.quotes import CollectorGap


class FakeRpc:
    def __init__(self, head: int = 100) -> None:
        self.head = head
        self.calls: list[str] = []

    def block_number(self) -> int:
        return self.head

    def get_block(self, number: int, full_txs: bool = False) -> dict[str, Any] | None:
        self.calls.append(f"block:{number}")
        return {"timestamp": hex(1_700_000_000 + number * 2)}

    def multicall(
        self, calls: list[Any], block: int | str = "latest", **_: Any
    ) -> list[Any]:
        return [(False, b"")] * len(calls)

    def get_logs(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def get_transaction_receipt(self, tx_hash: str) -> None:
        return None

    def eth_call(self, *args: Any, **kwargs: Any) -> str:
        return "0x" + "0" * 64


def test_chain_poller_starts_at_head_no_backfill() -> None:
    rpc = FakeRpc(head=50)
    poller = ChainPoller(
        rpc,  # type: ignore[arg-type]
        pools=[],
        lop_address="0x" + "11" * 20,
        head_lag_blocks=0,
    )
    n = poller.poll_once()
    assert n == 1
    assert poller.last_block == 50
    # Second poll with same head does nothing.
    assert poller.poll_once() == 0


def test_chain_poller_records_gap_on_long_lag() -> None:
    rpc = FakeRpc(head=10)
    gaps: list[CollectorGap] = []
    poller = ChainPoller(
        rpc,  # type: ignore[arg-type]
        pools=[],
        lop_address="0x" + "11" * 20,
        head_lag_blocks=0,
        max_catchup_blocks=3,
        on_gap=gaps.append,
    )
    poller.poll_once()  # last = 10
    rpc.head = 20  # lag=10 > max_catchup=3
    n = poller.poll_once()
    assert any(g.source == "mantle_blocks" for g in gaps)
    assert "skipping to tip" in gaps[0].detail
    # Processes only the recent window (max_catchup blocks), not full history.
    assert n == 3
    assert poller.last_block == 20
