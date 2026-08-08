"""Unit tests for WHI-909 cost-stack study helpers."""

from __future__ import annotations

from decimal import Decimal

from monitor.analysis.cost_stack import (
    basis_series_from_mids,
    kline_mid,
    parse_bybit_klines,
)
from monitor.analysis.edge_quant import usdc_premium_bps_from_mid
from monitor.metrics.edge import basis_wear_bps


class TestKlineParse:
    def test_mid_hl2(self) -> None:
        assert kline_mid(Decimal("1.001"), Decimal("0.999")) == Decimal("1.000")

    def test_parse_bybit_payload(self) -> None:
        payload = {
            "retCode": 0,
            "result": {
                "list": [
                    # newest first (Bybit convention); parser sorts ascending
                    ["2000", "1.0008", "1.0009", "1.0007", "1.0008", "0", "0"],
                    ["1000", "1.0006", "1.0007", "1.0005", "1.0006", "0", "0"],
                ]
            },
        }
        rows = parse_bybit_klines(payload)
        assert [r[0] for r in rows] == [1000, 2000]
        assert rows[0][1] == Decimal("1.0006")  # (1.0007+1.0005)/2


class TestBasisSeriesSignConvention:
    """Paying USDC is charged; receiving USDC is credited (WHI-960/909)."""

    def test_live_mid_matches_engine_sign(self) -> None:
        mid = Decimal("1.00075")
        premium = usdc_premium_bps_from_mid(mid)
        assert premium == Decimal("7.5")
        # Dir1 pays USDC → +premium wear; dir2 receives → −premium credit.
        assert basis_wear_bps(premium, "buy_fluxion_sell_bybit") == Decimal("7.5")
        assert basis_wear_bps(premium, "buy_bybit_sell_fluxion") == Decimal("-7.5")

    def test_series_from_mids(self) -> None:
        series = basis_series_from_mids(
            [(0, Decimal("1.00075")), (60_000, Decimal("1.001"))],
            source="test",
        )
        assert series.n == 2
        assert series.bps[0] == Decimal("7.5")
        assert series.bps[1] == Decimal("10")
        summary = series.summary()
        assert summary["n"] == 2
        assert Decimal(summary["min_bps"]) == Decimal("7.5")
