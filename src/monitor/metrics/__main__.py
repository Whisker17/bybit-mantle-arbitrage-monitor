"""CLI: one-shot PnL v2 bucket table + optimal size (WHI-756).

Synthetic mid-aligned demo by default (no journal). For live journal rows use
the pure functions from a notebook or future Web/API wiring.

Usage::

    uv run python -m monitor.metrics
    uv run python -m monitor.metrics --mid 100 --fluxion-mid 99.5 --size 1000
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from monitor.fluxion.pools import mid_from_sqrt_price_x96
from monitor.metrics.amm_pool import AmmPoolState
from monitor.metrics.config import load_metrics_config
from monitor.metrics.edge import Direction
from monitor.metrics.pnl_v2 import pnl_bucket_table


def _pool(mid: Decimal, pool_fee: int) -> AmmPoolState:
    ratio = Decimal(10) ** 12 / mid
    sqrt_price_x96 = int(ratio.sqrt() * Decimal(2**96))
    # Verify reconstructable.
    _ = mid_from_sqrt_price_x96(
        sqrt_price_x96,
        token0_is_quote=True,
        token0_decimals=6,
        token1_decimals=18,
    )
    return AmmPoolState(
        pool_fee=pool_fee,
        sqrt_price_x96=sqrt_price_x96,
        liquidity=10**20,
        token0_is_quote=True,
        token0_decimals=6,
        token1_decimals=18,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PnL v2 bucket table + optimal size")
    p.add_argument("--pair-id", default="DEMO")
    p.add_argument("--mid", type=Decimal, default=Decimal(100), help="Bybit L1 mid")
    p.add_argument(
        "--fluxion-mid",
        type=Decimal,
        default=None,
        help="Fluxion AMM mid (default: same as --mid)",
    )
    p.add_argument("--pool-fee", type=int, default=0, help="UniV3 fee units (3000=30bps)")
    p.add_argument(
        "--direction",
        choices=["buy_fluxion_sell_bybit", "buy_bybit_sell_fluxion"],
        default="buy_fluxion_sell_bybit",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON")
    args = p.parse_args(argv)

    cfg = load_metrics_config()
    bybit_mid = args.mid
    flux_mid = args.fluxion_mid if args.fluxion_mid is not None else bybit_mid
    amm = _pool(flux_mid, args.pool_fee)
    direction: Direction = args.direction

    table = pnl_bucket_table(
        pair_id=args.pair_id,
        bybit_bid=bybit_mid,
        bybit_ask=bybit_mid,
        direction=direction,
        config=cfg,
        amm=amm,
        include_optimal=True,
    )

    if args.json:
        json.dump(table.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print(
        f"pair={table.pair_id} direction={table.direction} "
        f"bybit_mid={bybit_mid} flux_mid={flux_mid}"
    )
    print("AMM buckets:")
    for row in table.amm_buckets:
        flag = "ok" if row.fillable else f"NO({row.reason})"
        print(
            f"  Q=${row.size_usd:>8}  pnl=${row.pnl_usd:>12.6f}  "
            f"gas=${row.costs.gas_usd}  {flag}"
        )
    if table.optimal is not None:
        o = table.optimal
        print(
            f"optimal: Q*=${o.q_star_usd}  pnl=${o.pnl_usd}  "
            f"samples={o.samples_evaluated}  q_max=${o.q_max_usd}"
        )
    else:
        print("optimal: none (no fillable size in range)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
