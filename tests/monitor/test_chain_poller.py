"""Seams: ChainPoller advances head, gap on long lag, no historical backfill."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from monitor.fluxion.chain import ChainPoller
from monitor.quotes import CollectorGap, DexPoolTvlTick


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


def test_chain_poller_block_not_found_emits_gap() -> None:
    class MissingBlockRpc(FakeRpc):
        def get_block(self, number: int, full_txs: bool = False) -> dict[str, Any] | None:
            self.calls.append(f"block:{number}")
            return None

    gaps: list[CollectorGap] = []
    rpc = MissingBlockRpc(head=10)
    poller = ChainPoller(
        rpc,  # type: ignore[arg-type]
        pools=[],
        lop_address="0x" + "11" * 20,
        head_lag_blocks=0,
        on_gap=gaps.append,
    )
    n = poller.poll_once()
    assert n == 0
    assert poller.last_block == 9  # primed to head-1, failed to advance
    assert any("not found" in g.detail for g in gaps)


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


def test_chain_poller_bsc_gap_source_and_empty_lop() -> None:
    """WHI-772: Pancake path uses gap_source=bsc_blocks and skips LOP when empty."""
    class CountingLogsRpc(FakeRpc):
        def get_logs(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            self.calls.append("get_logs")
            return []

    gaps: list[CollectorGap] = []
    rpc = CountingLogsRpc(head=10)
    poller = ChainPoller(
        rpc,  # type: ignore[arg-type]
        pools=[],
        lop_address="",  # AMM-only
        head_lag_blocks=0,
        max_catchup_blocks=2,
        gap_source="bsc_blocks",
        pool_state_every_n_blocks=2,
        on_gap=gaps.append,
    )
    poller.poll_once()  # last = 10
    # No LOP get_logs when lop_address is empty (pools empty → no swap logs either).
    assert "get_logs" not in rpc.calls
    rpc.head = 20
    poller.poll_once()
    assert any(g.source == "bsc_blocks" for g in gaps)


def test_chain_poller_pool_state_stride_still_bootstraps() -> None:
    """pool_state_every_n_blocks does not skip token bootstrap on first blocks."""
    from monitor.fluxion.pools import PoolMeta

    class PoolRpc(FakeRpc):
        def multicall(
            self, calls: list[Any], block: int | str = "latest", **_: Any
        ) -> list[Any]:
            self.calls.append(f"multicall:{len(calls)}")
            # slot0, liquidity, token0, token1 — no convert (has_erc4626=False).
            # Return failing so decode skips; we only check the call happened.
            return [(False, b"")] * len(calls)

    rpc = PoolRpc(head=5)
    meta = PoolMeta(
        pair_id="TSLAB",
        pool="0x" + "aa" * 20,
        wrapper_token="0x" + "bb" * 20,
        native_token="0x" + "bb" * 20,
        quote_token="0x" + "cc" * 20,
        quote_decimals=18,
        has_erc4626_wrapper=False,
    )
    poller = ChainPoller(
        rpc,  # type: ignore[arg-type]
        pools=[meta],
        lop_address="",
        head_lag_blocks=0,
        pool_state_every_n_blocks=2,
        gap_source="bsc_blocks",
    )
    poller.poll_once()  # processes block 5 — bootstrap wants pool state
    assert any(c.startswith("multicall:") for c in rpc.calls)


def test_tvl_due_requires_callback_and_interval() -> None:
    """WHI-782: TVL poll throttle is wall-clock; 0 or missing callback → off."""
    rpc = FakeRpc(head=10)
    poller = ChainPoller(
        rpc,  # type: ignore[arg-type]
        pools=[],
        lop_address="",
        tvl_poll_interval_s=30.0,
        on_pool_tvl=None,
    )
    assert poller._tvl_due() is False

    seen: list[list[DexPoolTvlTick]] = []
    poller2 = ChainPoller(
        rpc,  # type: ignore[arg-type]
        pools=[],
        lop_address="",
        tvl_poll_interval_s=0.0,
        on_pool_tvl=lambda t: seen.append(t),
    )
    assert poller2._tvl_due() is False

    poller3 = ChainPoller(
        rpc,  # type: ignore[arg-type]
        pools=[],
        lop_address="",
        tvl_poll_interval_s=30.0,
        on_pool_tvl=lambda t: seen.append(t),
    )
    assert poller3._tvl_due() is True
    poller3._last_tvl_poll_ms = 1_000_000
    with patch("monitor.fluxion.chain.now_ms", return_value=1_000_000 + 10_000):
        assert poller3._tvl_due() is False  # only 10s elapsed
    with patch("monitor.fluxion.chain.now_ms", return_value=1_000_000 + 31_000):
        assert poller3._tvl_due() is True


def test_tvl_throttle_advances_when_pool_state_empty() -> None:
    """Due TVL with failed pool decode still advances throttle (no every-block hammer)."""
    from monitor.fluxion.pools import PoolMeta

    class FailPoolRpc(FakeRpc):
        def multicall(
            self, calls: list[Any], block: int | str = "latest", **_: Any
        ) -> list[Any]:
            self.calls.append(f"multicall:{len(calls)}")
            return [(False, b"")] * len(calls)

    rpc = FailPoolRpc(head=20)
    meta = PoolMeta(
        pair_id="TSLAB",
        pool="0x" + "aa" * 20,
        wrapper_token="0x" + "bb" * 20,
        native_token="0x" + "bb" * 20,
        quote_token="0x" + "cc" * 20,
        quote_decimals=18,
        has_erc4626_wrapper=False,
    )
    tvl_batches: list[list[DexPoolTvlTick]] = []
    poller = ChainPoller(
        rpc,  # type: ignore[arg-type]
        pools=[meta],
        lop_address="",
        head_lag_blocks=0,
        pool_state_every_n_blocks=8,
        tvl_poll_interval_s=30.0,
        on_pool_tvl=lambda t: tvl_batches.append(t),
        gap_source="bsc_blocks",
    )
    # Prime last_block so next poll processes one block.
    poller._last_block = 19
    assert poller._tvl_due() is True
    poller.poll_once()
    assert poller._last_tvl_poll_ms is not None
    # Throttle advanced even though pool decode failed → no TVL ticks written.
    assert tvl_batches == []
    # Immediately after, TVL is not due again.
    assert poller._tvl_due() is False
