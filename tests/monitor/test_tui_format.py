"""Pure format helpers."""

from __future__ import annotations

from decimal import Decimal

from monitor.metrics.session import SessionKind
from monitor.tui.format import fmt_direction, fmt_signed_bps, short_addr, sparkline
from monitor.tui.model import SpreadPoint


def test_fmt_signed_bps() -> None:
    assert fmt_signed_bps(Decimal("12.3")) == "+12.3"
    assert fmt_signed_bps(Decimal("-1.5")) == "-1.5"
    assert fmt_signed_bps(None) == "—"


def test_fmt_direction() -> None:
    assert fmt_direction("buy_fluxion_sell_bybit") == "F→B"
    assert fmt_direction("buy_bybit_sell_fluxion") == "B→F"
    assert fmt_direction(None) == "—"


def test_short_addr() -> None:
    addr = "0x" + "ab" * 20
    assert short_addr(addr).startswith("0xababab")
    assert short_addr(addr).endswith("abab")


def test_sparkline_session_preserved() -> None:
    pts = [
        SpreadPoint(ts_ms=i, amm_spread_bps=Decimal(i), session=SessionKind.OPEN)
        for i in range(5)
    ] + [
        SpreadPoint(
            ts_ms=10 + i, amm_spread_bps=Decimal(i), session=SessionKind.CLOSED
        )
        for i in range(5)
    ]
    cells = sparkline(pts, width=10)
    assert len(cells) == 10
    assert any(s is SessionKind.OPEN for _, s in cells)
    assert any(s is SessionKind.CLOSED for _, s in cells)
