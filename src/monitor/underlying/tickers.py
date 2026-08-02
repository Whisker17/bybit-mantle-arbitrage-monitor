"""Pair id → canonical underlying ticker (WHI-778).

Tokenized symbols end in ``x`` (xStocks) or ``B`` (bStocks). Special cases:
``MUB`` → ``MU`` (Micron), ``SKHYB`` → ``SKHY``.
"""

from __future__ import annotations

# Explicit overrides before suffix stripping.
_PAIR_OVERRIDES: dict[str, str] = {
    "MUB": "MU",
    "SKHYB": "SKHY",
    "SPCXB": "SPCX",
    "SPCXx": "SPCX",
}


def pair_id_to_underlying_ticker(pair_id: str) -> str:
    """Map inventory ``pair_id`` to a canonical underlying ticker.

    Examples: ``AAPLx`` → ``AAPL``, ``AAPLB`` → ``AAPL``, ``TSLAB`` → ``TSLA``,
    ``MUB`` → ``MU``, ``SKHYB`` → ``SKHY``.
    """
    pid = pair_id.strip()
    if not pid:
        raise ValueError("pair_id must be non-empty")
    if pid in _PAIR_OVERRIDES:
        return _PAIR_OVERRIDES[pid]
    # xStocks: trailing lowercase x (AAPLx, TSLAx, SPCXx already handled).
    if len(pid) > 1 and pid[-1] == "x" and pid[-2].isupper():
        return pid[:-1]
    # bStocks: trailing B (AAPLB, NVDAB, …) — not single-letter tickers.
    if len(pid) > 1 and pid.endswith("B") and pid[:-1].isalpha():
        return pid[:-1]
    return pid


def underlying_tickers_for_pairs(pair_ids: list[str] | tuple[str, ...]) -> list[str]:
    """Unique canonical tickers for a market inventory, stable order."""
    seen: set[str] = set()
    out: list[str] = []
    for pid in pair_ids:
        t = pair_id_to_underlying_ticker(pid)
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
