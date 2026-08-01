"""Parse Bybit v5 public spot WS payloads into normalized ticks.

Pure functions only — mutable L1 merge state lives in ``l1.L1BookTracker``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from monitor.quotes import BybitTradeTick, now_ms
from monitor.symbols.multipliers import de_multiplied_price

# Default for tests / unconfigured callers. Production injects YAML
# (config/collector.yaml → orderbook.50 after WHI-755; L1 was orderbook.1 / WHI-743).
DEFAULT_BOOK_PREFIX = "orderbook.50"
DEFAULT_TRADE_PREFIX = "publicTrade"


def _dec(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _symbol_from_topic(topic: str) -> str:
    """Extract symbol from the last dotted segment (e.g. ``orderbook.1.SYMBOL``)."""
    if not topic or "." not in topic:
        return ""
    return topic.rsplit(".", 1)[-1].upper()


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_level_ops(levels: object) -> list[tuple[Decimal, Decimal]]:
    """Parse Bybit ``b``/``a`` entries into ``(price, size)`` ops (size 0 = delete)."""
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
        out.append((price, size))
    return out


@dataclass(frozen=True, slots=True)
class OrderbookL1Update:
    """One orderbook.1 push after pure parse (before per-symbol L1 merge)."""

    symbol: str
    msg_type: str  # "snapshot" | "delta" | ""
    # None = side key absent from ``data``; empty list = key present, no ops.
    bid_ops: list[tuple[Decimal, Decimal]] | None
    ask_ops: list[tuple[Decimal, Decimal]] | None
    u: int | None
    seq: int | None
    exchange_ts_ms: int | None


def parse_orderbook_l1_update(payload: dict[str, Any]) -> OrderbookL1Update | None:
    """Parse Bybit v5 ``orderbook.1.{symbol}`` into a typed L1 update (no merge)."""
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    symbol = str(data.get("s") or data.get("symbol") or "").upper()
    if not symbol:
        symbol = _symbol_from_topic(str(payload.get("topic") or ""))
    if not symbol:
        return None

    msg_type = str(payload.get("type") or "").lower()
    bid_ops = parse_level_ops(data["b"]) if "b" in data else None
    ask_ops = parse_level_ops(data["a"]) if "a" in data else None

    exchange_ts = data.get("ts") or payload.get("ts") or payload.get("cts")
    exchange_ts_ms: int | None
    if exchange_ts is None:
        exchange_ts_ms = None
    else:
        try:
            exchange_ts_ms = int(exchange_ts)
        except (TypeError, ValueError):
            exchange_ts_ms = None

    return OrderbookL1Update(
        symbol=symbol,
        msg_type=msg_type,
        bid_ops=bid_ops,
        ask_ops=ask_ops,
        u=_optional_int(data.get("u")),
        seq=_optional_int(data.get("seq")),
        exchange_ts_ms=exchange_ts_ms,
    )


def apply_l1_side(
    current: Decimal | None,
    ops: list[tuple[Decimal, Decimal]] | None,
    *,
    is_snapshot: bool,
    prefer_high: bool,
) -> Decimal | None:
    """Fold orderbook side ops onto L1 price.

    Snapshot replaces the side from the ops list (best of positive sizes).
    Delta applies every entry in order: size 0 deletes that price, size > 0
    sets/updates it — multi-entry moves like ``[[old,0],[new,sz]]`` work.
    """
    if ops is None:
        return None if is_snapshot else current

    if is_snapshot:
        best: Decimal | None = None
        for price, size in ops:
            if size <= 0 or price <= 0:
                continue
            if best is None:
                best = price
            elif prefer_high:
                best = max(best, price)
            else:
                best = min(best, price)
        return best

    # delta
    if not ops:
        return current
    price = current
    for p, s in ops:
        if p <= 0:
            # Skip malformed price entry; keep prior L1.
            continue
        if s <= 0:
            if price == p:
                price = None
        else:
            price = p
    return price


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
    book_prefix: str = DEFAULT_BOOK_PREFIX,
    trade_prefix: str = DEFAULT_TRADE_PREFIX,
) -> list[str]:
    """Bybit v5 subscribe topic list for L1 orderbook + public trades."""
    args: list[str] = []
    for sym in symbols:
        s = sym.upper()
        args.append(f"{book_prefix}.{s}")
        args.append(f"{trade_prefix}.{s}")
    return args
