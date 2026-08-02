"""Parse Binance public WS payloads into normalized ticks.

Protocol differences from Bybit live here; journal ticks reuse BybitBookTick /
BybitDepthTick / BybitTradeTick shapes (per-market SQLite, ADR-0001).

bStocks multiplier: **multiply** (BEP-677) into ``*_de_multiplied`` comparable columns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from monitor.quotes import BybitBookTick, BybitTradeTick, now_ms
from monitor.symbols.multipliers import multiplied_price

# Stream suffixes (combined-stream path segments are lower-case).
DEFAULT_BOOK_STREAM = "bookTicker"
DEFAULT_DEPTH_STREAM = "depth20@100ms"
DEFAULT_TRADE_STREAM = "aggTrade"


def _dec(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _symbol_from_stream(stream: str) -> str:
    """``tslabusdt@bookTicker`` → ``TSLABUSDT``."""
    if not stream or "@" not in stream:
        return ""
    return stream.split("@", 1)[0].upper()


def parse_book_ticker(
    payload: dict[str, Any],
    *,
    pair_id_by_symbol: Mapping[str, str],
    ui_multiplier_by_symbol: Mapping[str, Decimal],
    recv_ts_ms: int | None = None,
    gap: bool = False,
    stream: str = "",
) -> BybitBookTick | None:
    """Parse a Binance ``bookTicker`` event (raw or combined-stream data)."""
    data = payload.get("data", payload) if "data" in payload else payload
    if not isinstance(data, dict):
        return None
    symbol = str(data.get("s") or data.get("symbol") or "").upper()
    if not symbol:
        symbol = _symbol_from_stream(stream or str(payload.get("stream") or ""))
    if not symbol:
        return None
    pair_id = pair_id_by_symbol.get(symbol)
    mult = ui_multiplier_by_symbol.get(symbol)
    if pair_id is None or mult is None:
        return None
    try:
        bid = _dec(data.get("b") if data.get("b") is not None else data.get("bidPrice"))
        ask = _dec(data.get("a") if data.get("a") is not None else data.get("askPrice"))
    except (InvalidOperation, TypeError):
        return None
    if bid <= 0 or ask <= 0 or bid >= ask:
        return None
    recv = recv_ts_ms if recv_ts_ms is not None else now_ms()
    # Spot bookTicker has no event time (E/T); u is a sequence id, not ms epoch.
    # Use wall-clock recv so journal exchange_ts_ms stays a real timestamp axis.
    exchange_ts = data.get("E") or data.get("T")
    try:
        exchange_ts_ms = int(exchange_ts) if exchange_ts is not None else recv
    except (TypeError, ValueError):
        exchange_ts_ms = recv
    return BybitBookTick(
        pair_id=pair_id,
        symbol=symbol,
        exchange_ts_ms=exchange_ts_ms,
        recv_ts_ms=recv,
        bid=bid,
        ask=ask,
        bid_de_multiplied=multiplied_price(bid, mult),
        ask_de_multiplied=multiplied_price(ask, mult),
        multiplier=mult,
        gap=gap,
    )


def parse_depth_levels(levels: object) -> list[tuple[Decimal, Decimal]]:
    """Parse Binance ``bids``/``asks`` arrays into ``(price, size)``."""
    if not isinstance(levels, list):
        return []
    out: list[tuple[Decimal, Decimal]] = []
    for top in levels:
        if not isinstance(top, (list, tuple)) or len(top) < 2:
            continue
        try:
            price = _dec(top[0])
            size = _dec(top[1])
        except (InvalidOperation, TypeError):
            continue
        if price <= 0 or size <= 0:
            continue
        out.append((price, size))
    return out


def parse_partial_depth(
    payload: dict[str, Any],
    *,
    stream: str = "",
) -> tuple[str, list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]], int | None] | None:
    """Parse ``depthN@100ms`` partial book.

    Returns ``(symbol, bids, asks, last_update_id)`` or None.
    """
    data = payload.get("data", payload) if "data" in payload else payload
    if not isinstance(data, dict):
        return None
    symbol = str(data.get("s") or data.get("symbol") or "").upper()
    if not symbol:
        symbol = _symbol_from_stream(stream or str(payload.get("stream") or ""))
    if not symbol:
        return None
    bids = parse_depth_levels(data.get("bids") or data.get("b"))
    asks = parse_depth_levels(data.get("asks") or data.get("a"))
    if not bids or not asks:
        return None
    last_id = data.get("lastUpdateId") or data.get("u")
    try:
        last_update_id = int(last_id) if last_id is not None else None
    except (TypeError, ValueError):
        last_update_id = None
    return symbol, bids, asks, last_update_id


def parse_agg_trade_message(
    payload: dict[str, Any],
    *,
    pair_id_by_symbol: Mapping[str, str],
    ui_multiplier_by_symbol: Mapping[str, Decimal],
    recv_ts_ms: int | None = None,
    gap: bool = False,
    stream: str = "",
) -> list[BybitTradeTick]:
    """Parse Binance ``aggTrade`` (single event or list)."""
    data = payload.get("data", payload) if "data" in payload else payload
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
        # Skip non-trade events that may share a stream envelope.
        event = str(item.get("e") or "").lower()
        if event and event != "aggtrade":
            continue
        symbol = str(item.get("s") or item.get("symbol") or "").upper()
        if not symbol:
            symbol = _symbol_from_stream(stream or str(payload.get("stream") or ""))
        pair_id = pair_id_by_symbol.get(symbol)
        mult = ui_multiplier_by_symbol.get(symbol)
        if pair_id is None or mult is None:
            continue
        try:
            price = _dec(item.get("p") if item.get("p") is not None else item.get("price"))
            size = _dec(item.get("q") if item.get("q") is not None else item.get("qty"))
        except (InvalidOperation, TypeError):
            continue
        if price <= 0 or size <= 0:
            continue
        # m = true → buyer is market maker → trade is a sell of the base.
        is_buyer_maker = item.get("m")
        if is_buyer_maker is True or str(is_buyer_maker).lower() == "true":
            side: str = "Sell"
        else:
            side = "Buy"
        trade_id = str(
            item.get("a")  # aggregate trade id
            or item.get("t")
            or item.get("tradeId")
            or ""
        )
        if not trade_id:
            trade_id = f"{item.get('T') or item.get('E')}:{price}:{size}:{side}"
        exchange_ts = item.get("T") or item.get("E") or payload.get("E") or recv
        out.append(
            BybitTradeTick(
                pair_id=pair_id,
                symbol=symbol,
                exchange_ts_ms=int(exchange_ts),
                recv_ts_ms=recv,
                trade_id=trade_id,
                price=price,
                price_de_multiplied=multiplied_price(price, mult),
                size=size,
                side=side,  # type: ignore[arg-type]
                multiplier=mult,
                gap=gap,
            )
        )
    return out


def build_stream_names(
    symbols: Sequence[str],
    *,
    book_stream: str = DEFAULT_BOOK_STREAM,
    depth_stream: str | None = DEFAULT_DEPTH_STREAM,
    trade_stream: str = DEFAULT_TRADE_STREAM,
    depth_enabled: bool = True,
) -> list[str]:
    """Lower-case combined-stream names for Binance public WS."""
    names: list[str] = []
    for sym in symbols:
        s = sym.lower()
        names.append(f"{s}@{book_stream}")
        if depth_enabled and depth_stream:
            names.append(f"{s}@{depth_stream}")
        names.append(f"{s}@{trade_stream}")
    return names


def build_combined_stream_path(
    symbols: Sequence[str],
    *,
    book_stream: str = DEFAULT_BOOK_STREAM,
    depth_stream: str | None = DEFAULT_DEPTH_STREAM,
    trade_stream: str = DEFAULT_TRADE_STREAM,
    depth_enabled: bool = True,
) -> str:
    """Path + query for combined streams: ``/stream?streams=a/b/c``."""
    names = build_stream_names(
        symbols,
        book_stream=book_stream,
        depth_stream=depth_stream,
        trade_stream=trade_stream,
        depth_enabled=depth_enabled,
    )
    return "/stream?streams=" + "/".join(names)


def stream_kind(stream: str) -> str:
    """Classify a combined-stream name: book | depth | trade | unknown."""
    lower = stream.lower()
    if "@" not in lower:
        return "unknown"
    suffix = lower.split("@", 1)[1]
    if suffix == "bookticker" or suffix.startswith("bookticker"):
        return "book"
    if suffix.startswith("depth"):
        return "depth"
    if suffix in ("aggtrade", "trade"):
        return "trade"
    return "unknown"
