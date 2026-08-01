"""Parse Bybit v5 public spot WS payloads into normalized ticks."""

from __future__ import annotations

import time
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from monitor.quotes import BybitBookTick, BybitTradeTick
from monitor.symbols.multipliers import de_multiplied_price


def symbol_to_pair_id(symbol_by_pair: Mapping[str, str], symbol: str) -> str | None:
    """Map Bybit symbol → pair_id using a prebuilt reverse map."""
    return symbol_by_pair.get(symbol.upper())


def _dec(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def parse_orderbook_message(
    payload: dict[str, Any],
    *,
    pair_id_by_symbol: Mapping[str, str],
    multiplier_by_symbol: Mapping[str, Decimal],
    recv_ts_ms: int | None = None,
    gap: bool = False,
) -> BybitBookTick | None:
    """Parse orderbook.1 snapshot/delta that carries best bid/ask.

    Bybit orderbook.1 data shape::
        {"s": "TSLAXUSDT", "b": [["price", "size"], ...], "a": [...], "ts": ...}
    Topic messages wrap data under ``data`` with optional ``ts`` / ``cts``.
    """
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    symbol = str(data.get("s") or data.get("symbol") or "").upper()
    if not symbol:
        # topic form: orderbook.1.TSLAXUSDT
        topic = str(payload.get("topic") or "")
        if topic.startswith("orderbook."):
            symbol = topic.rsplit(".", 1)[-1].upper()
    pair_id = pair_id_by_symbol.get(symbol)
    mult = multiplier_by_symbol.get(symbol)
    if pair_id is None or mult is None:
        return None

    bids = data.get("b") or data.get("bids") or []
    asks = data.get("a") or data.get("asks") or []
    if not bids or not asks:
        return None
    try:
        bid = _dec(bids[0][0])
        ask = _dec(asks[0][0])
    except (IndexError, TypeError, InvalidOperation, KeyError):
        return None
    if bid <= 0 or ask <= 0:
        return None

    exchange_ts = data.get("ts") or payload.get("ts") or payload.get("cts")
    if exchange_ts is None:
        exchange_ts = recv_ts_ms if recv_ts_ms is not None else _now_ms()
    exchange_ts_ms = int(exchange_ts)
    recv = recv_ts_ms if recv_ts_ms is not None else _now_ms()

    return BybitBookTick(
        pair_id=pair_id,
        symbol=symbol,
        exchange_ts_ms=exchange_ts_ms,
        recv_ts_ms=recv,
        bid=bid,
        ask=ask,
        bid_de_multiplied=de_multiplied_price(bid, mult),
        ask_de_multiplied=de_multiplied_price(ask, mult),
        multiplier=mult,
        gap=gap,
    )


def parse_public_trade_message(
    payload: dict[str, Any],
    *,
    pair_id_by_symbol: Mapping[str, str],
    multiplier_by_symbol: Mapping[str, Decimal],
    recv_ts_ms: int | None = None,
    gap: bool = False,
) -> list[BybitTradeTick]:
    """Parse publicTrade topic data (list of prints)."""
    data = payload.get("data", payload)
    if isinstance(data, dict):
        items: list[Any] = [data]
    elif isinstance(data, list):
        items = data
    else:
        return []

    recv = recv_ts_ms if recv_ts_ms is not None else _now_ms()
    out: list[BybitTradeTick] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("s") or item.get("symbol") or "").upper()
        if not symbol:
            topic = str(payload.get("topic") or "")
            if topic.startswith("publicTrade."):
                symbol = topic.rsplit(".", 1)[-1].upper()
        pair_id = pair_id_by_symbol.get(symbol)
        mult = multiplier_by_symbol.get(symbol)
        if pair_id is None or mult is None:
            continue
        try:
            price = _dec(item.get("p") if item.get("p") is not None else item.get("price"))
            size = _dec(item.get("v") if item.get("v") is not None else item.get("size"))
        except (InvalidOperation, TypeError):
            continue
        if price <= 0 or size <= 0:
            continue
        side_raw = str(item.get("S") or item.get("side") or "").capitalize()
        if side_raw not in ("Buy", "Sell"):
            continue
        trade_id = str(item.get("i") or item.get("tradeId") or item.get("execId") or "")
        if not trade_id:
            # Fall back to ts+price+size so UNIQUE still dedupes within a process.
            trade_id = f"{item.get('T') or item.get('ts')}:{price}:{size}:{side_raw}"
        exchange_ts = item.get("T") or item.get("ts") or payload.get("ts") or recv
        out.append(
            BybitTradeTick(
                pair_id=pair_id,
                symbol=symbol,
                exchange_ts_ms=int(exchange_ts),
                recv_ts_ms=recv,
                trade_id=trade_id,
                price=price,
                price_de_multiplied=de_multiplied_price(price, mult),
                size=size,
                side=side_raw,  # type: ignore[arg-type]
                multiplier=mult,
                gap=gap,
            )
        )
    return out


def build_subscribe_args(
    symbols: list[str],
    *,
    book_prefix: str = "orderbook.1",
    trade_prefix: str = "publicTrade",
) -> list[str]:
    """Bybit v5 subscribe topic list for L1 book + public trades."""
    args: list[str] = []
    for sym in symbols:
        s = sym.upper()
        args.append(f"{book_prefix}.{s}")
        args.append(f"{trade_prefix}.{s}")
    return args


def _now_ms() -> int:
    return int(time.time() * 1000)
