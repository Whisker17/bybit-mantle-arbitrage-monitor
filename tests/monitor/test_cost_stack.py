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
        assert kline_mid(
            Decimal("1"), Decimal("1.001"), Decimal("0.999"), Decimal("1")
        ) == Decimal("1.000")

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

    def test_backward_pagination_covers_span(self, monkeypatch: object) -> None:
        """Simulated Bybit pages: each call returns the newest ≤1000 in window."""
        from monitor.analysis import cost_stack as cs

        # 2500 one-minute bars: ts = 0, 60000, ..., 2499*60000
        all_bars = [
            [str(i * 60_000), "1.0007", "1.0008", "1.0006", "1.0007", "0", "0"]
            for i in range(2500)
        ]

        calls: list[tuple[int, int]] = []

        class _Resp:
            def __init__(self, body: bytes) -> None:
                self._body = body

            def read(self) -> bytes:
                return self._body

            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def fake_urlopen(req: object, timeout: float = 0) -> _Resp:  # noqa: ARG001
            import json
            import urllib.parse
            from urllib.request import Request

            assert isinstance(req, Request)
            qs = urllib.parse.urlparse(req.full_url).query
            params = urllib.parse.parse_qs(qs)
            start = int(params["start"][0])
            end = int(params["end"][0])
            calls.append((start, end))
            # Newest-first among bars with start_ms in [start, end], limit 1000.
            in_range = [b for b in all_bars if start <= int(b[0]) <= end]
            page = list(reversed(in_range[-1000:]))  # newest first
            body = json.dumps(
                {"retCode": 0, "retMsg": "OK", "result": {"list": page}}
            ).encode()
            return _Resp(body)

        monkeypatch.setattr(cs.urllib.request, "urlopen", fake_urlopen)  # type: ignore[attr-defined]
        rows = cs.fetch_usdcusdt_klines(start_ms=0, end_ms=2499 * 60_000)
        assert len(rows) == 2500
        assert rows[0][0] == 0
        assert rows[-1][0] == 2499 * 60_000
        assert len(calls) >= 3  # needed >1 page


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
