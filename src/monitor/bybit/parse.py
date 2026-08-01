"""Parse Bybit v5 public spot WS payloads into normalized ticks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from monitor.quotes import BybitBookTick, BybitTradeTick, now_ms
from monitor.symbols.multipliers import de_multiplied_price

# Spot public stream: tickers has no bid1/ask1; L1 is orderbook.1 (WHI-743 / WHI-630).
DEFAULT_BOOK_PREFIX = "orderbook.1"
DEFAULT_TRADE_PREFIX = "publicTrade"

SideAction = Literal["set", "clear", "unchanged"]


def _dec(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _symbol_from_topic(topic: str) -> str:
    """Extract symbol from ``orderbook.1.SYMBOL`` / ``tickers.SYMBOL`` / ``publicTrade.SYMBOL``."""
    if not topic or "." not in topic:
        return ""
    return topic.rsplit(".", 1)[-1].upper()


def _side_from_levels(
    levels: object,
    *,
    key_present: bool,
    is_snapshot: bool,
) -> tuple[SideAction, Decimal | None]:
    """Interpret one side of Bybit orderbook.1 ``b`` / ``a``.

    Snapshot replaces the local book; empty ladder clears the side.
    Delta empty ladder means no change; size ``0`` deletes the level (clear L1).
    """
    if not key_present:
        return ("clear", None) if is_snapshot else ("unchanged", None)
    if not isinstance(levels, list) or not levels:
        return ("clear", None) if is_snapshot else ("unchanged", None)
    top = levels[0]
    if not isinstance(top, (list, tuple)) or len(top) < 2:
        return ("unchanged", None)
    try:
        price = _dec(top[0])
        size = _dec(top[1])
    except (InvalidOperation, TypeError):
        return ("unchanged", None)
    if size <= 0:
        return ("clear", None)
    if price <= 0:
        return ("unchanged", None)
    return ("set", price)


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class OrderbookL1Update:
    """One orderbook.1 push after pure parse (before per-symbol L1 merge)."""

    symbol: str
    msg_type: str  # "snapshot" | "delta" | ""
    bid_action: SideAction
    ask_action: SideAction
    bid: Decimal | None
    ask: Decimal | None
    u: int | None
    seq: int | None
    exchange_ts_ms: int | None


def parse_orderbook_l1_update(payload: dict[str, Any]) -> OrderbookL1Update | None:
    """Parse Bybit v5 ``orderbook.1.{symbol}`` into a typed L1 side update."""
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    symbol = str(data.get("s") or data.get("symbol") or "").upper()
    if not symbol:
        symbol = _symbol_from_topic(str(payload.get("topic") or ""))
    if not symbol:
        return None

    msg_type = str(payload.get("type") or "").lower()
    is_snapshot = msg_type == "snapshot"
    bid_action, bid = _side_from_levels(
        data.get("b"),
        key_present="b" in data,
        is_snapshot=is_snapshot,
    )
    ask_action, ask = _side_from_levels(
        data.get("a"),
        key_present="a" in data,
        is_snapshot=is_snapshot,
    )
    exchange_ts = data.get("ts") or payload.get("ts") or payload.get("cts")
    return OrderbookL1Update(
        symbol=symbol,
        msg_type=msg_type,
        bid_action=bid_action,
        ask_action=ask_action,
        bid=bid,
        ask=ask,
        u=_optional_int(data.get("u")),
        seq=_optional_int(data.get("seq")),
        exchange_ts_ms=int(exchange_ts) if exchange_ts is not None else None,
    )


@dataclass(slots=True)
class _L1State:
    bid: Decimal | None = None
    ask: Decimal | None = None
    last_u: int | None = None
    last_seq: int | None = None


class L1BookTracker:
    """Per-symbol L1 book: merge snapshot/delta, drop non-increasing ``u``/``seq``."""

    def __init__(
        self,
        *,
        pair_id_by_symbol: Mapping[str, str],
        multiplier_by_symbol: Mapping[str, Decimal],
    ) -> None:
        self._pair_id_by_symbol = dict(pair_id_by_symbol)
        self._multiplier_by_symbol = dict(multiplier_by_symbol)
        self._states: dict[str, _L1State] = {}

    def apply(
        self,
        payload: dict[str, Any],
        *,
        recv_ts_ms: int | None = None,
        gap: bool = False,
    ) -> BybitBookTick | None:
        update = parse_orderbook_l1_update(payload)
        if update is None:
            return None
        pair_id = self._pair_id_by_symbol.get(update.symbol)
        mult = self._multiplier_by_symbol.get(update.symbol)
        if pair_id is None or mult is None:
            return None

        state = self._states.get(update.symbol)
        if state is None:
            state = _L1State()
            self._states[update.symbol] = state

        is_snapshot = update.msg_type == "snapshot"
        if not is_snapshot and not self._is_in_order(state, update):
            return None

        if is_snapshot:
            state.bid = None
            state.ask = None

        if update.bid_action == "set":
            state.bid = update.bid
        elif update.bid_action == "clear":
            state.bid = None

        if update.ask_action == "set":
            state.ask = update.ask
        elif update.ask_action == "clear":
            state.ask = None

        if update.u is not None:
            state.last_u = update.u
        if update.seq is not None:
            state.last_seq = update.seq

        if state.bid is None or state.ask is None:
            return None
        if state.bid <= 0 or state.ask <= 0:
            return None

        recv = recv_ts_ms if recv_ts_ms is not None else now_ms()
        exchange_ts = update.exchange_ts_ms if update.exchange_ts_ms is not None else recv
        return BybitBookTick(
            pair_id=pair_id,
            symbol=update.symbol,
            exchange_ts_ms=exchange_ts,
            recv_ts_ms=recv,
            bid=state.bid,
            ask=state.ask,
            bid_de_multiplied=de_multiplied_price(state.bid, mult),
            ask_de_multiplied=de_multiplied_price(state.ask, mult),
            multiplier=mult,
            gap=gap,
        )

    @staticmethod
    def _is_in_order(state: _L1State, update: OrderbookL1Update) -> bool:
        """Accept delta only when ``u`` (prefer) or ``seq`` is strictly increasing."""
        if update.u is not None and state.last_u is not None:
            return update.u > state.last_u
        if update.seq is not None and state.last_seq is not None:
            return update.seq > state.last_seq
        return True


def parse_ticker_message(
    payload: dict[str, Any],
    *,
    pair_id_by_symbol: Mapping[str, str],
    multiplier_by_symbol: Mapping[str, Decimal],
    recv_ts_ms: int | None = None,
    gap: bool = False,
    tracker: L1BookTracker | None = None,
) -> BybitBookTick | None:
    """Parse orderbook.1 into a book tick (optional shared ``L1BookTracker`` for merge).

    Without a tracker, uses a fresh one so a single complete push still works in unit tests.
    """
    book = tracker or L1BookTracker(
        pair_id_by_symbol=pair_id_by_symbol,
        multiplier_by_symbol=multiplier_by_symbol,
    )
    return book.apply(payload, recv_ts_ms=recv_ts_ms, gap=gap)


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
