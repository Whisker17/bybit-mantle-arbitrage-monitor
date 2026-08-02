"""Smoke CLI: fetch underlying prices once and print JSON lines.

Usage::

    uv run python -m monitor.underlying
    uv run python -m monitor.underlying --tickers AAPL,TSLA,SKHY
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from monitor.underlying.config import load_underlying_config
from monitor.underlying.poller import UnderlyingPoller


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="WHI-778 underlying price smoke fetch")
    p.add_argument(
        "--tickers",
        default="",
        help="Comma-separated subset (default: all covered)",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Path to underlying.yaml (default: config/underlying.yaml)",
    )
    args = p.parse_args(argv)

    cfg = load_underlying_config(None if args.config is None else Path(args.config))
    tickers = (
        [t.strip() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else cfg.covered_tickers()
    )
    poller = UnderlyingPoller(cfg, tickers=tickers)
    try:
        ticks = poller.poll_once()
    finally:
        poller.close()
    for t in ticks:
        print(
            json.dumps(
                {
                    "ticker": t.ticker,
                    "price": str(t.price),
                    "currency": t.currency,
                    "price_type": t.price_type,
                    "as_of_ms": t.as_of_ms,
                    "recv_ts_ms": t.recv_ts_ms,
                    "source": t.source,
                    "feed_id": t.feed_id,
                    "gap": t.gap,
                }
            )
        )
    got = {t.ticker for t in ticks}
    missing = [t for t in tickers if t not in got and not cfg.tickers[t].uncovered]
    if missing:
        print(f"# missing covered tickers: {missing}", file=sys.stderr)
        return 1
    uncovered = cfg.uncovered_tickers()
    if uncovered:
        print(f"# uncovered (expected empty): {uncovered}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
