# WHI-822: SPYB +1128 bps / +$294 PnL — investigation

**Date:** 2026-08-03  
**Issue:** WHI-822  
**Market:** `binance-pancake`  
**Verdict:** Inventory / decimals / multiplier are **correct**. The AMM mid is
the deviant leg. Panel must not claim a fillable paper arb when
`|AMM − CEX| / CEX` exceeds the configured guard
(`max_abs_amm_spread_bps`, default 500).

## Observed panel (issue sample)

| Leg | Mid | Notes |
|-----|-----|--------|
| Binance SPYBUSDT | 751.71 | bookTicker mid |
| Pancake V3 SPYB/USDT | 836.50 | journal `mid_usdc_per_native` |
| vs CEX | **+1127.9 bps** | `(836.50 − 751.71) / 751.71` |
| TVL | ~$227k | live balanceOf |
| PnL v2 (pre-fix) | status=`ok`, optimal ~$9.7k, net **+$294** | now blocked |

Control: QQQB CEX 692.31 / AMM 692.51 / +2.8 bps / TVL $2.0M — normal magnitude.

## 1. Pool ownership (on-chain)

| Field | Value | Source |
|-------|--------|--------|
| Inventory BEP-20 | `0x7138b48df7D98D7e3cc221BfE7192D0a178182D8` | `config/markets/binance-pancake.yaml` |
| Inventory pool | `0x7aA6d92Fc369A8C1EDc631A3aAc44eFB0808ddbF` fee=100 | same |
| `token0()` | USDT `0x55d3…7955` | eth_call latest |
| `token1()` | SPYB `0x7138…82D8` | eth_call latest |
| `symbol()` / `name()` | `SPYB` / `SPY` | eth_call |
| Enum snapshot | `verified: true`, same addresses | `docs/references/m7-bstocks-enum-snapshot.json` |
| DexScreener | same pool as primary SPYB/USDT | API 2026-08-03 |

**Conclusion:** No 张冠李戴. Pool tokens match the Binance capital BEP-20 and
the WHI-790 factory enum.

## 2. Decimals and price direction

| Token | decimals() | Inventory |
|-------|------------|-----------|
| SPYB | 18 | `native_decimals: 18` |
| USDT (BSC) | 18 | market `quote_decimals: 18` |

`mid_from_sqrt_price_x96` with `token0_is_quote=True` returns **USDT per SPYB**.
Live recompute from `slot0.sqrtPriceX96` matches journal mids and DexScreener
`priceUsd` (within block movement). Direction is not inverted.

## 3. Multiplier (BEP-677)

| Call | Raw | Human |
|------|-----|--------|
| `uiMultiplier()` | `1e18` | **1.0** |
| Config `ui_multiplier` | `'1'` | multiply once into comparable CEX columns |

No double-apply: CEX journal `*_de_multiplied` = display × 1; AMM is already
raw/on-chain space. Premium path divides uiMultiplier back for equity units.

## 4. Underlying cross-check

| Source | Price (2026-08-03 probe) |
|--------|---------------------------|
| Yahoo SPY | **747.03** USD |
| Binance SPYB | **~751.2** (tracks underlying, small premium) |
| AMM mid | **~1050–1200+** (thrashing block-to-block) |

**The AMM is the deviant side.** CEX tracks the equity; the pool does not.

## 5. Full collector-scope AMM pair audit (journal immutable snapshot)

21 pairs with pool state. Flag `***` = `|bps| > 500` **or** empty residual:

| pair | cex | amm | L | vs CEX bps | note |
|------|-----|-----|---|------------|------|
| SPYB | 751.4 | **1080.7** | >0 | **+4383** | **only live L>0 extreme** |
| MRVLB | 187.8 | 208.2 | 0 | +1086 | empty_pool (WHI-795) |
| KORUB | 15.6 | ~0 | 0 | −10000 | empty_pool |
| MUUB | 23.0 | ~0 | 0 | −10000 | empty_pool |
| AMZNB | 275.4 | 271.9 | >0 | −129 | under guard |
| others | — | — | >0 | typically \|bps\| < 100 | normal |

Healthy liquid names (QQQB, NVDAB, TSLAB, SPCXB, …) sit near 0–30 bps.

## 6. Why PnL v2 looked fillable (and why we still guard)

- Pool balances (probe): **~0.37 SPYB** + **~$214k USDT** — almost pure quote
  inventory; DexScreener liq ~$214k and **24h vol multi‑millions** (active
  thrashing at a detached mid).
- Single-range V3 math can still report `fillable=True` for notionals that
  stay inside `_MAX_SQRT_MOVE_FRAC` of the current sqrt price even when the
  mid itself is not an equity-fair quote.
- Product rule (issue): **prefer miss over a fake tradable opportunity**.

This is **not** the WHI-795 empty-pool residual class (L=0). Guard is a
separate reason code: `pricing_anomaly`.

## 7. Product fix (this PR)

1. Config `max_abs_amm_spread_bps: 500` (`config/metrics.yaml`).
2. Seam `annotate_pricing_anomaly` after `quotable_amm_mid`.
3. Keep mid + spread visible; set `amm_quote_reason=pricing_anomaly`.
4. Suppress paper edge ladder, PnL v2 optimal (`status=pricing_anomaly`),
   and Web Top-N seats / badges (`price anomaly`).

## 8. Not claimed

- That a human can free-arb $294 risk-free (withdraw delays, range exits,
  adversarial flow, and binance deposit path not executed here).
- That the pool is “broken code” — measurements are consistent across RPC,
  journal, and DexScreener.

If a future day shows SPYB inside the guard with independent fill evidence,
document that sample separately; do not lower the guard without product sign-off.
