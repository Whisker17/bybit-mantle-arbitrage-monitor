"""Seam: mechanism_for_swap / mechanism_for_rfq_fill."""

from __future__ import annotations

from decimal import Decimal

from monitor.attribution import (
    Mechanism,
    amm_trade_from_swap,
    mechanism_for_rfq_fill,
    mechanism_for_swap,
    rfq_fill_from_tick,
    swap_notional_usd,
)
from monitor.metrics.session import SessionKind
from monitor.quotes import FluxionRfqFillTick, FluxionSwapTick


def _swap(**overrides: object) -> FluxionSwapTick:
    base: dict[str, object] = {
        "pair_id": "AAPLx",
        "pool": "0xpool",
        "block_number": 1,
        "block_ts": 1_700_000_000,
        "recv_ts_ms": 1_700_000_000_000,
        "tx_hash": "0xtx",
        "log_index": 0,
        "sender": "0x" + "11" * 20,
        "recipient": "0x" + "22" * 20,
        "amount0": 1,
        "amount1": -1,
        "sqrt_price_x96": 1,
        "liquidity": 1,
        "tick": 0,
        "amount_token0": Decimal(100),
        "amount_token1": Decimal(-1),
        "direction": "buy_native",
        "price_usdc_per_wrapper": Decimal(100),
    }
    base.update(overrides)
    return FluxionSwapTick(**base)  # type: ignore[arg-type]


def _rfq(**overrides: object) -> FluxionRfqFillTick:
    base: dict[str, object] = {
        "block_number": 2,
        "block_ts": 1_700_000_100,
        "recv_ts_ms": 1_700_000_100_000,
        "tx_hash": "0xrfq",
        "log_index": 1,
        "order_hash": "0xorder",
        "remaining_making_amount": 0,
    }
    base.update(overrides)
    return FluxionRfqFillTick(**base)  # type: ignore[arg-type]


def test_swap_is_amm_mechanism() -> None:
    assert mechanism_for_swap(_swap()) is Mechanism.AMM


def test_rfq_fill_is_rfq_mechanism() -> None:
    assert mechanism_for_rfq_fill(_rfq()) is Mechanism.RFQ


def test_builders_preserve_mechanism() -> None:
    trade = amm_trade_from_swap(
        _swap(),
        notional_usd=Decimal(100),
        fluxion_mid_pre=Decimal(100),
        bybit_mid=Decimal(101),
        session=SessionKind.OPEN,
    )
    assert trade is not None
    assert mechanism_for_swap(trade) is Mechanism.AMM

    fill = rfq_fill_from_tick(_rfq(), pair_id="AAPLx")
    assert fill.pair_id == "AAPLx"
    assert mechanism_for_rfq_fill(fill) is Mechanism.RFQ


def test_unknown_direction_swap_not_converted() -> None:
    trade = amm_trade_from_swap(
        _swap(direction="unknown"),
        notional_usd=Decimal(10),
        fluxion_mid_pre=None,
        bybit_mid=None,
        session=SessionKind.CLOSED,
    )
    assert trade is None


def test_swap_notional_usd_uses_quote_leg() -> None:
    s = _swap(amount_token0=Decimal("-50"), amount_token1=Decimal("1"))
    assert swap_notional_usd(s, quote_is_token0=True) == Decimal(50)
    assert swap_notional_usd(s, quote_is_token0=False) == Decimal(1)
