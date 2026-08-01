"""Display formatting helpers (pure; Textual applies styles separately)."""

from __future__ import annotations

from decimal import Decimal

from monitor.metrics.edge import Direction
from monitor.metrics.session import SessionKind
from monitor.tui.model import PairOverviewRow, SpreadPoint


def fmt_price(value: Decimal | None, *, digits: int = 4) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def fmt_bps(value: Decimal | None, *, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def fmt_signed_bps(value: Decimal | None, *, digits: int = 1) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{digits}f}"


def fmt_notional(value: Decimal | None) -> str:
    if value is None:
        return "—"
    v = float(value)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return f"{v:.0f}"


def fmt_direction(direction: Direction | None) -> str:
    if direction is None:
        return "—"
    if direction == "buy_fluxion_sell_bybit":
        return "F→B"
    return "B→F"


def fmt_session(session: SessionKind | None) -> str:
    if session is None:
        return "?"
    if session is SessionKind.OPEN:
        return "OPEN"
    return "CLOSED"


def short_addr(addr: str | None, *, head: int = 6, tail: int = 4) -> str:
    if not addr:
        return "—"
    if len(addr) <= head + tail + 2:
        return addr
    return f"{addr[: head + 2]}…{addr[-tail:]}"


def sparkline(
    points: list[SpreadPoint],
    *,
    width: int,
    field: str = "amm_spread_bps",
) -> list[tuple[str, SessionKind | None]]:
    """Return ``width`` (char, session) cells for a vertical-bar sparkline.

    Characters are chosen from a fixed block set; session is preserved so the
    UI can color open vs closed. Empty series → blank cells.
    """
    blocks = "▁▂▃▄▅▆▇█"
    if width <= 0:
        return []
    values: list[tuple[Decimal | None, SessionKind]] = [
        (getattr(p, field), p.session) for p in points
    ]
    if not values:
        return [(" ", None)] * width
    # downsample to width
    if len(values) > width:
        step = (len(values) - 1) / (width - 1) if width > 1 else 0
        sampled = [values[round(i * step)] for i in range(width)]
    else:
        sampled = values
        # pad left
        pad = width - len(sampled)
        sampled = [(None, sampled[0][1])] * pad + sampled

    nums = [v for v, _ in sampled if v is not None]
    if not nums:
        return [(" ", s) for _, s in sampled]
    lo = min(nums)
    hi = max(nums)
    span = hi - lo
    out: list[tuple[str, SessionKind | None]] = []
    for v, sess in sampled:
        if v is None:
            out.append((" ", sess))
            continue
        if span == 0:
            idx = len(blocks) // 2
        else:
            ratio = float((v - lo) / span)
            idx = max(0, min(len(blocks) - 1, int(ratio * (len(blocks) - 1))))
        out.append((blocks[idx], sess))
    return out


def sort_rows(
    rows: list[PairOverviewRow],
    *,
    key: str,
    desc: bool,
) -> list[PairOverviewRow]:
    """Stable sort with None / missing values sorted last regardless of direction."""

    def raw(row: PairOverviewRow) -> Decimal | str | int | None:
        mapping: dict[str, Decimal | str | int | None] = {
            "pair_id": row.pair_id,
            "net_edge": row.net_edge_bps,
            "amm_spread": row.amm_spread_bps,
            "rfq_spread": row.rfq_spread_bps,
            "bybit_mid": row.bybit_mid,
            "volume_24h": row.volume_24h,
            "trades_24h": row.trades_24h,
        }
        return mapping.get(key, row.pair_id)

    def sort_key(row: PairOverviewRow) -> tuple[float | str | int]:
        val = raw(row)
        # present list below only includes non-None values
        if isinstance(val, Decimal):
            return (float(val),)
        if val is None:
            return (0,)
        return (val,)

    present = [r for r in rows if raw(r) is not None]
    missing = [r for r in rows if raw(r) is None]
    present_sorted = sorted(present, key=sort_key, reverse=desc)
    return present_sorted + missing
