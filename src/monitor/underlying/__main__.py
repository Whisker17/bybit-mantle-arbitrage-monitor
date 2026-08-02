"""Smoke CLI: fetch underlying prices once and print JSON lines.

Usage::

    uv run python -m monitor.underlying
    uv run python -m monitor.underlying --tickers AAPL,TSLA,SKHY,SPCX
    uv run python -m monitor.underlying --probe-uncovered
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from monitor.underlying.config import load_underlying_config
from monitor.underlying.coverage_probe import UncoveredCoverageProbe
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
    p.add_argument(
        "--probe-uncovered",
        action="store_true",
        help="WHI-787/794: probe uncovered reverse mismatches + never-published "
        "Hermes feeds (standalone; does not require the collector)",
    )
    args = p.parse_args(argv)

    cfg = load_underlying_config(None if args.config is None else Path(args.config))

    if args.probe_uncovered:
        probe = UncoveredCoverageProbe(cfg)
        try:
            outcome = probe.probe_once()
        finally:
            probe.close()
        for m in outcome.mismatches:
            print(json.dumps({"kind": "mismatch", **m.to_dict()}))
        for u in outcome.unpublished_feeds:
            # WHI-794: configured Hermes feed never published.
            print(json.dumps({"kind": "unpublished_feed", **u.to_dict()}))
        for e in outcome.errors:
            print(json.dumps({"kind": "error", **e.to_dict()}), file=sys.stderr)
        if not cfg.uncovered_tickers():
            print("# no uncovered tickers in config", file=sys.stderr)
        # Exit 1 only for actionable hits: uncovered reverse mismatch, or
        # unpublished feed without Yahoo gap-fill. Known unpublished pins that
        # already have yahoo_symbol stay advisory (exit 0).
        need_yahoo = [
            u
            for u in outcome.unpublished_feeds
            if not (cfg.tickers.get(u.ticker) and cfg.tickers[u.ticker].yahoo_symbol)
        ]
        if outcome.mismatches or need_yahoo:
            return 1
        if outcome.inconclusive:
            return 2
        return 0

    if args.tickers:
        requested = [t.strip() for t in args.tickers.split(",") if t.strip()]
        unknown = [t for t in requested if t not in cfg.tickers]
        if unknown:
            print(f"# unknown tickers (not in config): {unknown}", file=sys.stderr)
            return 2
        tickers = requested
    else:
        tickers = cfg.covered_tickers()
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
    missing = [
        t
        for t in tickers
        if t not in got and t in cfg.tickers and not cfg.tickers[t].uncovered
    ]
    if missing:
        print(f"# missing covered tickers: {missing}", file=sys.stderr)
        return 1
    uncovered = [t for t in tickers if t in cfg.tickers and cfg.tickers[t].uncovered]
    if not args.tickers:
        uncovered = cfg.uncovered_tickers()
    if uncovered:
        print(f"# uncovered (expected empty): {uncovered}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
