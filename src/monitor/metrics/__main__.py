"""CLI: one-shot PnL v2 bucket table + optimal size (WHI-756 / WHI-773).

Synthetic mid-aligned demo by default (no journal). Venue costs come from
``config/markets/{id}.yaml`` via ``--market``; algorithm knobs from
``config/metrics.yaml``.

Usage::

    uv run python -m monitor.metrics
    uv run python -m monitor.metrics --market binance-pancake --mid 100 --amm-mid 99.5
    uv run python -m monitor.metrics --mid 100 --fluxion-mid 99.5 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from monitor.metrics.amm_pool import AmmPoolState
from monitor.metrics.edge import Direction
from monitor.metrics.pnl_v2 import pnl_bucket_table


def _pool(
    mid: Decimal,
    pool_fee: int,
    *,
    quote_decimals: int,
    base_decimals: int,
) -> AmmPoolState:
    """Deep synthetic pool at ``mid`` quote/base (hand-checkable slip ≈ 0)."""
    # sqrt(P) with P = token1/token0 raw adjusted for decimals:
    # mid_quote_per_base = f(sqrt, token0_is_quote, decimals).
    # token0=quote → ratio raw = 10^(base-quote) / mid.
    exp = base_decimals - quote_decimals
    ratio = (Decimal(10) ** exp) / mid
    sqrt_price_x96 = int(ratio.sqrt() * Decimal(2**96))
    return AmmPoolState(
        pool_fee=pool_fee,
        sqrt_price_x96=sqrt_price_x96,
        liquidity=10**20,
        token0_is_quote=True,
        token0_decimals=quote_decimals,
        token1_decimals=base_decimals,
    )


def _resolve_pool_fee_and_decimals(
    market_id: str,
    pair_id: str,
    pool_fee_arg: int | None,
) -> tuple[int, int, int]:
    """Return (pool_fee, quote_decimals, base_decimals) for the synthetic pool."""
    from monitor.markets import load_market_context

    ctx = load_market_context(market_id, load_collector=False)
    # Defaults: Fluxion-style USDC 6 / base 18; Pancake USDT 18 / base 18.
    if ctx.bstocks is not None:
        quote_dec, base_dec = 18, 18
        fee = 0 if pool_fee_arg is None else pool_fee_arg
        if pool_fee_arg is None and pair_id != "DEMO":
            try:
                bp = ctx.bstocks.pair_by_id(pair_id)
            except KeyError:
                bp = None
            if bp is not None and bp.pancake.amm is not None:
                fee = bp.pancake.amm.fee
                base_dec = bp.pancake.native_decimals
        return fee, quote_dec, base_dec

    quote_dec, base_dec = 6, 18
    fee = 0 if pool_fee_arg is None else pool_fee_arg
    if pool_fee_arg is None and ctx.pairs is not None and pair_id != "DEMO":
        try:
            pair = ctx.pairs.pair_by_id(pair_id)
        except KeyError:
            pair = None
        if pair is not None and pair.fluxion.amm is not None:
            fee = pair.fluxion.amm.fee
    return fee, quote_dec, base_dec


def main(argv: list[str] | None = None) -> int:
    from monitor.markets import DEFAULT_MARKET_ID, load_market_context

    p = argparse.ArgumentParser(description="PnL v2 bucket table + optimal size")
    p.add_argument(
        "--market",
        default=DEFAULT_MARKET_ID,
        help=f"Market id for fee/gas costs (default: {DEFAULT_MARKET_ID})",
    )
    p.add_argument("--pair-id", default="DEMO")
    p.add_argument(
        "--mid",
        type=Decimal,
        default=Decimal(100),
        help="CEX L1 mid in comparable space (de-multiplied / multiplied)",
    )
    p.add_argument(
        "--amm-mid",
        "--fluxion-mid",
        dest="amm_mid",
        type=Decimal,
        default=None,
        help="AMM mid quote/base (default: same as --mid)",
    )
    p.add_argument(
        "--pool-fee",
        type=int,
        default=None,
        help="UniV3 fee units (3000=30bps). Default: 0, or inventory fee when --pair-id set",
    )
    p.add_argument(
        "--direction",
        choices=["buy_fluxion_sell_bybit", "buy_bybit_sell_fluxion"],
        default="buy_fluxion_sell_bybit",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON")
    args = p.parse_args(argv)

    # Venue costs come from the market file; algorithm knobs from metrics.yaml.
    ctx = load_market_context(args.market, load_collector=False)
    cfg = ctx.metrics
    cex_mid = args.mid
    amm_mid = args.amm_mid if args.amm_mid is not None else cex_mid
    pool_fee, quote_dec, base_dec = _resolve_pool_fee_and_decimals(
        args.market, args.pair_id, args.pool_fee
    )
    amm = _pool(
        amm_mid, pool_fee, quote_decimals=quote_dec, base_decimals=base_dec
    )
    direction: Direction = args.direction

    table = pnl_bucket_table(
        pair_id=args.pair_id,
        bybit_bid=cex_mid,
        bybit_ask=cex_mid,
        direction=direction,
        config=cfg,
        amm=amm,
        include_optimal=True,
    )

    if args.json:
        payload = table.to_dict()
        payload["market"] = ctx.market_id
        payload["cex_taker_fee_bps"] = str(cfg.bybit_taker_fee_bps)
        payload["gas_usd_per_swap"] = str(cfg.gas_usd_per_swap)
        payload["pool_fee"] = pool_fee
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print(
        f"market={ctx.market_id} pair={table.pair_id} direction={table.direction} "
        f"cex_mid={cex_mid} amm_mid={amm_mid} pool_fee={pool_fee} "
        f"taker_bps={cfg.bybit_taker_fee_bps} gas=${cfg.gas_usd_per_swap}"
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
