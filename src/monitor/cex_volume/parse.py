"""Pure parsers for CEX 24h ticker REST payloads (WHI-777)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal


class CexVolumeParseError(ValueError):
    """Malformed ticker payload."""


def _as_decimal(raw: object, *, field: str) -> Decimal:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CexVolumeParseError(f"{field} is not a number: {raw!r}") from exc


def _as_optional_int(raw: object) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


def parse_bybit_tickers(
    payload: Any,
    *,
    pair_id_by_symbol: dict[str, str],
) -> list[tuple[str, str, Decimal, int | None]]:
    """Parse Bybit ``GET /v5/market/tickers`` JSON.

    Returns rows ``(pair_id, symbol, turnover24h, None)`` for symbols present
    in ``pair_id_by_symbol`` (keys uppercased). Trade count is not published
    on this endpoint.
    """
    if not isinstance(payload, dict):
        raise CexVolumeParseError(
            f"bybit tickers root must be object, got {type(payload).__name__}"
        )
    ret = payload.get("retCode", payload.get("ret_code"))
    if ret is not None and int(ret) != 0:
        raise CexVolumeParseError(
            f"bybit tickers retCode={ret} retMsg={payload.get('retMsg')!r}"
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise CexVolumeParseError("bybit tickers missing result object")
    rows = result.get("list")
    if not isinstance(rows, list):
        raise CexVolumeParseError("bybit tickers result.list must be a list")

    wanted = {k.upper(): v for k, v in pair_id_by_symbol.items()}
    out: list[tuple[str, str, Decimal, int | None]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        sym = str(item.get("symbol") or "").upper()
        if not sym or sym not in wanted:
            continue
        if "turnover24h" not in item:
            raise CexVolumeParseError(f"bybit ticker {sym} missing turnover24h")
        vol = _as_decimal(item["turnover24h"], field=f"{sym}.turnover24h")
        if vol < 0:
            raise CexVolumeParseError(f"{sym}.turnover24h must be >= 0, got {vol}")
        out.append((wanted[sym], sym, vol, None))
    return out


def parse_binance_ticker_24hr(
    payload: Any,
    *,
    pair_id_by_symbol: dict[str, str],
) -> list[tuple[str, str, Decimal, int | None]]:
    """Parse Binance ``GET /api/v3/ticker/24hr`` JSON (object or list).

    Returns ``(pair_id, symbol, quoteVolume, count)``.
    """
    if isinstance(payload, dict):
        items: list[Any] = [payload]
    elif isinstance(payload, list):
        items = payload
    else:
        raise CexVolumeParseError(
            f"binance 24hr root must be object or list, got {type(payload).__name__}"
        )

    wanted = {k.upper(): v for k, v in pair_id_by_symbol.items()}
    out: list[tuple[str, str, Decimal, int | None]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sym = str(item.get("symbol") or "").upper()
        if not sym or sym not in wanted:
            continue
        if "quoteVolume" not in item:
            raise CexVolumeParseError(f"binance ticker {sym} missing quoteVolume")
        vol = _as_decimal(item["quoteVolume"], field=f"{sym}.quoteVolume")
        if vol < 0:
            raise CexVolumeParseError(f"{sym}.quoteVolume must be >= 0, got {vol}")
        count = _as_optional_int(item.get("count"))
        out.append((wanted[sym], sym, vol, count))
    return out


def venue_source(venue: Literal["bybit", "binance"]) -> Literal["bybit", "binance"]:
    return venue
