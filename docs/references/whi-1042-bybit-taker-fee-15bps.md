# WHI-1042 — Bybit xStocks fee tier: maker 10 / taker 15 (monitor)

**Date:** 2026-08-11  
**Issue:** WHI-1042  
**Config:** `config/markets/bybit-fluxion.yaml` → `costs.cex_taker_fee_bps`  
**Base default:** `config/metrics.yaml` → `bybit_taker_fee_bps`  
**Sibling:** execution bot (`mantle-stocks-arbitrage-bots`) shipped the same
pin earlier in this issue as `costs.bybit_taker_fee_bps` (PR #71).

## Measurement

Live `GET /v5/account/fee-rate` (read-only key), 2026-08-11, all seven
Fluxion-liquid xStocks pairs:

| Symbol | makerFeeRate | takerFeeRate |
| --- | ---: | ---: |
| AAPLX … TSLAX (all seven) | 0.0010 | 0.0015 |

→ **maker 10 bps / taker 15 bps**.

Corroborating fills (`/v5/execution/list`): tier flipped 2026-08-07 between
03:13 (METAx buy still **20** bps) and 13:19 (CRCLx sell **15** taker). Full
fill table lives in Linear WHI-1042 / sibling bot evidence note.

## Config change

| Field | Was (WHI-959) | Now (WHI-1042) |
| --- | ---: | ---: |
| `costs.cex_taker_fee_bps` (bybit-fluxion) | 20 | **15** |
| `bybit_taker_fee_bps` (metrics base) | 20 | **15** |
| `binance-pancake` `cex_taker_fee_bps` | 10 | **unchanged** |

## Decisions

### 1. Maker rate is **not** a config knob

Monitor paper edge always charges **taker** (crossing side). Realized maker
fills are free edge vs the model. Do not add `cex_maker_fee_bps` until a
fill-mode prediction exists (monitor is read-only — even less reason to model
resting limits). Logged in `docs/DEFERRED_ISSUES.md`.

### 2. Staleness: dated `MEASURED` comment, not periodic re-read

Prefer a dated comment + endpoint next to the constant. Monitor has no
trading-role private REST bootstrap; re-read would need a new read-only key
path. Second wrong pin in a week (WHI-959 → WHI-1042) — re-check after Bybit
VIP/zone announcements.

### 3. Historical research notes

M8 edge-quant / delay-decay papers that used taker **20** remain historically
correct for those archives. Live bybit-fluxion stack is 5 bps lighter; do not
rewrite M0 verdict numbers in this PR.
