# M1 xStocks inventory notes (WHI-730)

Companion to `config/pairs.yaml`. Snapshot date: **2026-07-31**.

## Method

1. **Bybit overlap candidates** — spot instruments with `symbolType == "xstocks"` and
   quote `USDT`. Field `xstockMultiplier` is the corporate-action multiplier
   (DESIGN: de-multiply Bybit mid before comparing to Fluxion).
2. **Mantle native tokens** — Backed Assets API `GET https://api.backed.fi/api/v1/token`,
   filter `deployments.network == "Mantle"` and symbol ending in `x`.
3. **Fluxion AMM pools** — Fluxion V3 factory `getPool(wrapper, USDC|USDT0, fee)` for fee
   tiers `{100,500,3000,10000}`. Wrappers verified via ERC-4626 `asset() == native`.
   Liquidity estimate: `2 * quote_token.balanceOf(pool)` (USDC 6 decimals ≈ USD), matching
   Fluxion’s published agent skill method. All liquid pools found were **USDC / fee 3000**.
4. **V2 scope** — Fluxion product docs mention AMM V2, but the public agent skill and
   contract pin only a **V3 factory** (`0xF883…737C`). No V2 factory address is published
   for xStock inventory. All xStock pools discovered were **V3**; `amm: null` means “no
   V3 wrapper/USDC|USDT0 pool in the scanned fee tiers,” not a V2 scan. If a V2 factory
   is published later, re-inventory and extend `AmmPool.kind`.
5. **RFQ** — see `m1-rfq-feasibility.md` (pollable public quote).

## Overlap set (in `config/pairs.yaml`)

| Pair | Bybit | Multiplier (snap) | AMM pool | Est TVL (USD) | `low_liquidity` |
|------|-------|-------------------|----------|---------------|-----------------|
| AAPLx | AAPLXUSDT | 1.002664… | yes V3/3000 | ~112k | no |
| CRCLx | CRCLXUSDT | 1 | yes V3/3000 | ~80k | no |
| GOOGLx | GOOGLXUSDT | 1.001927… | yes V3/3000 | ~111k | no |
| HOODx | HOODXUSDT | 1 | yes V3/3000 | ~111k | no |
| METAx | METAXUSDT | 1.002298… | yes V3/3000 | ~100k | no |
| NVDAx | NVDAXUSDT | 1.000918… | yes V3/3000 | ~106k | no |
| TSLAx | TSLAXUSDT | 1 | yes V3/3000 | ~94k | no |
| SPCXx | SPCXXUSDT | 1 | yes V3/3000 | ~15 | **yes** |
| AMZNx | AMZNXUSDT | 1 | none found | 0 | **yes** |
| COINx | COINXUSDT | 1 | none found | 0 | **yes** |
| MCDx | MCDXUSDT | 1.016160… | none found | 0 | **yes** |

Low-liquidity rows are **kept** (issue requirement). AMM-null rows still have Mantle
native + Bybit listing; RFQ can quote native/USDC.

## Not in the fixed list (intentional)

| Asset | Why omitted |
|-------|-------------|
| MSTRx, SPYx, QQQx | Liquid Fluxion V3 pools exist (~85–109k est.) but **no Bybit xstocks spot** at inventory time → not a dual-venue overlap pair. |
| MSFTx | Mantle native exists; no Bybit xstocks spot; no pool found in scan. |
| 600+ other Mantle xStocks | No Bybit spot listing; out of scope for dual-venue panel. |

Revisit when Bybit lists additional symbols or when product expands to Fluxion-only
display.

## ABI / fork lineage (open risk §8)

Fluxion V3 is **Uniswap V3–lineage** (factory `getPool`, fee tiers 100/500/3000/10000,
published `v3-core` repo). QuoterV2 at `0x3E4eE18Ac7280813236a1EB850679Da5322E14CE`.
M2 should still re-verify pool `slot0` / topic0 against a live swap before trusting any
lifted Agni-era decoding from phase-1 (Agni ≠ vanilla UniV3 was the phase-1 trap;
Fluxion documents itself as UniV3-compatible, which is a better starting point than Agni).

## Multiplier application

```
comparable_bybit_mid = bybit_token_mid / xstock_multiplier
```

Implemented in `monitor.symbols.de_multiplied_price`. Snapshot multipliers live in YAML;
M2 may refresh from instruments-info without changing pair identity.
