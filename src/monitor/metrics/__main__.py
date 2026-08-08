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
from typing import TYPE_CHECKING

from monitor.fluxion.abi import WRAPPER_DECIMALS_DEFAULT
from monitor.metrics.amm_pool import AmmPoolState
from monitor.metrics.edge import Direction
from monitor.metrics.pnl_v2 import pnl_bucket_table

if TYPE_CHECKING:
    from monitor.markets.context import MarketContext


def _pool(
    mid: Decimal,
    pool_fee: int,
    *,
    quote_decimals: int,
    base_decimals: int,
) -> AmmPoolState:
    """Deep synthetic pool at ``mid`` quote/base (hand-checkable slip ≈ 0)."""
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


def _inventory_pool_fee_and_base_decimals(
    ctx: MarketContext,
    pair_id: str,
) -> tuple[int, int] | None:
    """Look up (pool_fee, base_decimals) from whichever inventory the market has.

    Returns None when the pair id is absent or has no AMM — caller decides
    whether that is an error.
    """
    if ctx.bstocks is not None:
        try:
            bpair = ctx.bstocks.pair_by_id(pair_id)
        except KeyError:
            return None
        if bpair.pancake.amm is None:
            return None
        return bpair.pancake.amm.fee, bpair.pancake.native_decimals
    if ctx.pairs is not None:
        try:
            fpair = ctx.pairs.pair_by_id(pair_id)
        except KeyError:
            return None
        if fpair.fluxion.amm is None:
            return None
        # Wrapper pool uses default base decimals (same as monitor.fluxion.pools).
        return fpair.fluxion.amm.fee, WRAPPER_DECIMALS_DEFAULT
    return None


def main(argv: list[str] | None = None) -> int:
    from monitor.markets import DEFAULT_MARKET_ID, load_market_context

    p = argparse.ArgumentParser(description="PnL v2 bucket table + optimal size")
    p.add_argument(
        "--market",
        default=DEFAULT_MARKET_ID,
        help=f"Market id for fee/gas costs (default: {DEFAULT_MARKET_ID})",
    )
    p.add_argument(
        "--pair-id",
        default=None,
        help="Inventory pair id (loads pool fee / base decimals). Omit for synthetic DEMO.",
    )
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
        help="UniV3 fee units (3000=30bps). Default: inventory fee when --pair-id set, else 0",
    )
    p.add_argument(
        "--direction",
        choices=["buy_fluxion_sell_bybit", "buy_bybit_sell_fluxion"],
        default="buy_fluxion_sell_bybit",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON")
    args = p.parse_args(argv)

    ctx = load_market_context(args.market, load_collector=False)
    cfg = ctx.metrics
    cex_mid = args.mid
    amm_mid = args.amm_mid if args.amm_mid is not None else cex_mid

    # Quote decimals always from market file (config-injected).
    quote_dec = ctx.dex.quote_decimals
    base_dec = WRAPPER_DECIMALS_DEFAULT
    pool_fee = 0 if args.pool_fee is None else args.pool_fee
    pair_id = args.pair_id or "DEMO"

    if args.pair_id is not None:
        looked = _inventory_pool_fee_and_base_decimals(ctx, args.pair_id)
        if looked is None:
            print(
                f"error: pair {args.pair_id!r} not found (or has no AMM) "
                f"in market {ctx.market_id}",
                file=sys.stderr,
            )
            return 2
        inv_fee, base_dec = looked
        if args.pool_fee is None:
            pool_fee = inv_fee

    amm = _pool(
        amm_mid, pool_fee, quote_decimals=quote_dec, base_decimals=base_dec
    )
    direction: Direction = args.direction

    fee_tokens = None
    mult = Decimal(1)
    if args.pair_id is not None:
        inv_pair = None
        if ctx.pairs is not None:
            try:
                inv_pair = ctx.pairs.pair_by_id(args.pair_id)
            except KeyError:
                inv_pair = None
        if inv_pair is None and ctx.bstocks is not None:
            try:
                inv_pair = ctx.bstocks.pair_by_id(args.pair_id)
            except KeyError:
                inv_pair = None
        if inv_pair is not None:
            from monitor.metrics.withdrawal import withdrawal_params_from_pair

            wd = withdrawal_params_from_pair(inv_pair)
            fee_tokens = wd.asset_fee_tokens
            mult = wd.price_multiplier

    table = pnl_bucket_table(
        pair_id=pair_id,
        bybit_bid=cex_mid,
        bybit_ask=cex_mid,
        direction=direction,
        config=cfg,
        amm=amm,
        include_optimal=True,
        asset_withdrawal_fee_tokens=fee_tokens,
        price_multiplier=mult,
    )

    if args.json:
        payload = table.to_dict()
        payload["market"] = ctx.market_id
        payload["cex_taker_fee_bps"] = str(cfg.bybit_taker_fee_bps)
        payload["gas_usd_per_swap"] = str(cfg.gas_usd_per_swap)
        payload["pool_fee"] = pool_fee
        payload["quote_decimals"] = quote_dec
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
