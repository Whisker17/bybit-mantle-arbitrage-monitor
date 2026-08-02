"""WHI-777: REST ticker parse fixtures for Bybit + Binance 24h volume."""

from __future__ import annotations

from decimal import Decimal

import pytest

from monitor.cex_volume.parse import (
    CexVolumeParseError,
    parse_binance_ticker_24hr,
    parse_bybit_tickers,
)


def test_parse_bybit_tickers_filters_and_reads_turnover() -> None:
    payload = {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [
                {
                    "symbol": "TSLAXUSDT",
                    "turnover24h": "1234567.89",
                    "volume24h": "100",
                },
                {
                    "symbol": "OTHERUSDT",
                    "turnover24h": "999",
                },
            ]
        },
    }
    rows = parse_bybit_tickers(
        payload, pair_id_by_symbol={"TSLAXUSDT": "TSLAx"}
    )
    assert len(rows) == 1
    pair_id, symbol, vol, count = rows[0]
    assert pair_id == "TSLAx"
    assert symbol == "TSLAXUSDT"
    assert vol == Decimal("1234567.89")
    assert count is None


def test_parse_bybit_tickers_rejects_nonzero_retcode() -> None:
    with pytest.raises(CexVolumeParseError, match="retCode"):
        parse_bybit_tickers(
            {"retCode": 10001, "retMsg": "nope", "result": {"list": []}},
            pair_id_by_symbol={"A": "a"},
        )


def test_parse_binance_ticker_list_quote_volume_and_count() -> None:
    payload = [
        {
            "symbol": "TSLABUSDT",
            "quoteVolume": "50000.5",
            "count": 42,
            "volume": "10",
        },
        {"symbol": "SKIP", "quoteVolume": "1", "count": 1},
    ]
    rows = parse_binance_ticker_24hr(
        payload, pair_id_by_symbol={"tslabusdt": "TSLAB"}
    )
    assert len(rows) == 1
    pair_id, symbol, vol, count = rows[0]
    assert pair_id == "TSLAB"
    assert symbol == "TSLABUSDT"
    assert vol == Decimal("50000.5")
    assert count == 42


def test_parse_binance_single_object() -> None:
    rows = parse_binance_ticker_24hr(
        {"symbol": "AUSDT", "quoteVolume": "1.5", "count": 3},
        pair_id_by_symbol={"AUSDT": "A"},
    )
    assert rows == [("A", "AUSDT", Decimal("1.5"), 3)]


def test_parse_binance_missing_quote_volume() -> None:
    with pytest.raises(CexVolumeParseError, match="quoteVolume"):
        parse_binance_ticker_24hr(
            {"symbol": "AUSDT", "volume": "1"},
            pair_id_by_symbol={"AUSDT": "A"},
        )
