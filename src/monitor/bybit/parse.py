"""Parse Bybit v5 public spot WS payloads into normalized ticks."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from monitor.quotes import BybitBookTick, BybitTradeTick, now_ms
from monitor.symbols.multipliers import de_multiplied_price

# Spot public stream: tickers has no bid1/ask1; L1 is orderbook.1 (WHI-743 / WHI-630).
_DEFAULT_BOOK_PREFIX = "orderbook.1"
_DEFAULT_TRADE_PREFIX = "publicTrade"


def _dec(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _best_level_price(levels: object) -> Decimal | None:
    """Top-of-book price from Bybit orderbook ``b`` / ``a`` (list of [price, size])."""
    if not isinstance(levels, list) or not levels:
        return None
    top = levels[0]
    if not isinstance(top, (list, tuple)) or len(top) < 1:
        return None
    try:
        price = _dec(top[0])
        size = _dec(top[1]) if len(top) > 1 else Decimal("1")
    except (InvalidOperation, TypeError, IndexError):
        return None
    if price <= 0 or size <= 0:
        return None
    return price


def _symbol_from_topic(topic: str) -> str:
    """Extract symbol from ``orderbook.1.SYMBOL`` or ``tickers.SYMBOL``."""
    if not topic:
        return ""
    for prefix in (_DEFAULT_BOOK_PREFIX, "tickers"):
        p = f"{prefix}."
        if topic.startswith(p):
            return topic[len(p) :].upper()
    # Fallback: last dotted segment (publicTrade.SYMBOL, etc.)
    if "." in topic:
        return topic.rsplit(".", 1)[-1].upper()
    return ""


def parse_ticker_message(
    payload: dict[str, Any],
    *,
    pair_id_by_symbol: Mapping[str, str],
    multiplier_by_symbol: Mapping[str, Decimal],
    recv_ts_ms: int | None = None,
    gap: bool = False,
) -> BybitBookTick | None:
    """Parse Bybit v5 L1 book from ``orderbook.1.{symbol}`` (a/b levels).

    Spot public ``tickers.{symbol}`` has no bid1/ask1; use orderbook depth 1.
    At depth=1 both snapshot and delta carry the full top level (WHI-743).
    Legacy linear/tickers ``bid1Price``/``ask1Price`` is still accepted if present.
    """
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    symbol = str(data.get("symbol") or data.get("s") or "").upper()
    if not symbol:
        symbol = _symbol_from_topic(str(payload.get("topic") or ""))
    pair_id = pair_id_by_symbol.get(symbol)
    mult = multiplier_by_symbol.get(symbol)
    if pair_id is None or mult is None:
        return None

    bid = _best_level_price(data.get("b"))
    ask = _best_level_price(data.get("a"))
    if bid is None or ask is None:
        # Legacy tickers / linear style (not present on spot public tickers).
        bid_raw = data.get("bid1Price")
        ask_raw = data.get("ask1Price")
        if bid_raw is None or ask_raw is None or bid_raw == "" or ask_raw == "":
            return None
        try:
            bid = _dec(bid_raw)
            ask = _dec(ask_raw)
        except (InvalidOperation, TypeError):
            return None
    if bid <= 0 or ask <= 0:
        return None

    exchange_ts = data.get("ts") or payload.get("ts") or payload.get("cts")
    recv = recv_ts_ms if recv_ts_ms is not None else now_ms()
    if exchange_ts is None:
        exchange_ts = recv

    return BybitBookTick(
        pair_id=pair_id,
        symbol=symbol,
        exchange_ts_ms=int(exchange_ts),
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

    recv = recv_ts_ms if recv_ts_ms is not None else now_ms()
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
    book_prefix: str = _DEFAULT_BOOK_PREFIX,
    trade_prefix: str = _DEFAULT_TRADE_PREFIX,
) -> list[str]:
    """Bybit v5 subscribe topic list for L1 orderbook + public trades."""
    args: list[str] = []
    for sym in symbols:
        s = sym.upper()
        args.append(f"{book_prefix}.{s}")
        args.append(f"{trade_prefix}.{s}")
    return args
