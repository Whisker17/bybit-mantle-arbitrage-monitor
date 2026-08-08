# Delay-decay: sequential transfer cycle edge (WHI-915 / M8)

Does the bot's **zero-inventory transfer cycle** still earn money when the legs are sequential? Fluxion buy at opportunity-window open, Bybit sell at `t + N`, full cost stack including the flat recycle withdraw fee. Companion to the simultaneous M0 report (`docs/references/m8-xstocks-edge-quant.md`).

**Generated:** 2026-08-07 03:39:40 UTC

## Regeneration

```bash
uv run python scripts/xstocks_delay_decay.py --db data/monitor-bybit-fluxion.db --sample-ms 20000
```

Pure helpers: `monitor.analysis.delay_decay` (unit-tested).
Venue math: `monitor.metrics.pnl_v2.compute_pnl_usd` + delayed Bybit sell.

## Method

| Item | Value |
|------|--------|
| market | bybit-fluxion |
| direction | buy_fluxion_sell_bybit |
| engine | pnl_v2 + delayed Bybit sell |
| analysis | monitor.analysis.delay_decay |
| clip_sizes_usd | 100, 250, 500, 1000 |
| lags_min | 5, 10, 15, 20, 30, 60 |
| primary_lag_min | 10 |
| primary_size_usd | 500 |
| withdraw_fee_usd | 1 |
| sample_ms | 20000 |
| align_ms | 15000 |
| reentry_cooldown_ms | 86400000 |
| trade_duration_ms | 5000 |
| max_gap_ms | 120000 |
| pricing_anomaly_gate | 500 (study-time default; **WHI-964** shipped config is **300**) |
| sigma_align_ms | max(align_ms, sample_ms) |

### Cycle reconstruction

1. Detect opportunity windows on **simultaneous** net edge (`buy_fluxion_sell_bybit` only) at each clip size; fire on window open.
2. At entry: executable Fluxion buy (fee + impact) and same-timestamp Bybit sell (baseline simultaneous PnL).
3. At `t + N`: Bybit sell VWAP for the **same base qty**, fee-inclusive.
4. Realised PnL = delayed Bybit proceeds − Fluxion spend − gas − USDT/USDC basis − flat withdraw fee.
5. Transit σ: sample std of Bybit mid log-returns over each lag (session-split).
6. `drift_premium_k`: minimal k on a 0.1 grid such that `simultaneous_edge ≥ k · σ` yields the target realised win rate (min 5 admitted samples).

## Data span

Study window wall clock: **2026-08-05 03:19:03 UTC → 2026-08-07 03:38:46 UTC** (2.014 d). RTH excl. gap: **5.37 h**. Delayed sells may resolve up to 60 min past the book max timestamp.

| Table | Rows | Min (UTC) | Max (UTC) |
|-------|-----:|-----------|-----------|
| `bybit_book` | 1,000,151 | 2026-08-05 03:19:03 UTC | 2026-08-07 03:38:46 UTC |
| `bybit_depth` | 754,614 | 2026-08-05 03:19:03 UTC | 2026-08-07 03:38:47 UTC |
| `fluxion_pool_state` | 899,272 | 2026-08-02 03:18:24 UTC | 2026-08-07 03:38:46 UTC |
| `collector_gaps` | 3,735 | 2026-08-02 03:18:32 UTC | 2026-08-07 02:51:08 UTC |

## Headline (sequential vs simultaneous)

| Metric | Value |
|--------|------:|
| Study calendar days | 2.014 |
| Primary lag | 10 min |
| Primary clip | $500 |
| Withdraw fee (flat) | $1 / cycle |
| Simultaneous portfolio $/day (baseline, no withdraw fee) | 0.7387 |
| Sequential portfolio $/day (realised, w/ withdraw fee) | 4.4503 |
| Sequential / simultaneous ratio | 6.025 |
| Sequential go-list (open, primary) | CRCLx, HOODx, NVDAx |
| Symbols clearing ≥1 USDT/day sequential | NVDAx |
| **Verdict** | **NO-GO (sequential)** |

Sequential portfolio **4.4503 USDT/day** is below the 5 no-go floor. Simultaneous baseline was 0.7387 USDT/day. Do not live-trade on this span.

> **Headline caveat:** the sequential/simultaneous ratio is **not** an M0 apples-to-apples edge comparison. Simultaneous here is direction-1 fire-on-open paper PnL on the **same sparse windows** (n open windows is small on this span); sequential adds favourable or adverse transit drift plus the flat withdraw fee. A ratio > 1 usually means the delayed sell luckily improved a few cycles — not that delay is free. See universe note below.

## Measured transit-window σ (Bybit mid log-return, bps)

Replaces the ~40 bps / 10 min HOODx estimate. 1σ sample std; RTH and closed reported separately.

| Symbol | Session | N=5m | N=10m | N=15m | N=20m | N=30m | N=60m | n@10m |
|--------|---------|-----:|------:|------:|------:|------:|------:|------:|
| AAPLx | open | 17.47 | 24.85 | 31.67 | 37.31 | 45.92 | 60.08 | 708 |
| AAPLx | closed | 6.87 | 9.84 | 11.77 | 13.44 | 15.42 | 19.53 | 2176 |
| AAPLx | all | 10.90 | 16.58 | 20.66 | 23.08 | 26.34 | 32.14 | 2909 |
| CRCLx | open | 54.72 | 68.83 | 78.94 | 81.27 | 80.00 | 106.21 | 715 |
| CRCLx | closed | 36.91 | 58.57 | 79.55 | 97.06 | 117.58 | 146.98 | 2814 |
| CRCLx | all | 44.42 | 64.00 | 80.52 | 94.35 | 111.65 | 141.19 | 3553 |
| GOOGLx | open | 44.45 | 74.27 | 95.46 | 101.10 | 112.62 | 166.94 | 710 |
| GOOGLx | closed | 16.63 | 22.18 | 23.87 | 24.90 | 28.74 | 33.75 | 2232 |
| GOOGLx | all | 26.62 | 42.27 | 52.93 | 56.32 | 62.35 | 86.59 | 2969 |
| HOODx | open | 30.30 | 44.03 | 58.03 | 65.00 | 55.46 | 49.38 | 717 |
| HOODx | closed | 17.60 | 25.08 | 29.13 | 34.28 | 38.43 | 36.32 | 1955 |
| HOODx | all | 22.53 | 31.76 | 39.11 | 45.36 | 46.71 | 44.05 | 2697 |
| METAx | open | 18.56 | 22.36 | 26.09 | 28.31 | 31.46 | 53.82 | 699 |
| METAx | closed | 10.65 | 13.18 | 16.35 | 19.36 | 23.38 | 32.76 | 1894 |
| METAx | all | 14.42 | 19.19 | 22.64 | 24.57 | 29.06 | 44.21 | 2616 |
| NVDAx | open | 23.90 | 26.50 | 27.09 | 27.27 | 31.69 | 40.55 | 716 |
| NVDAx | closed | 9.84 | 14.19 | 18.22 | 21.88 | 26.94 | 32.40 | 3092 |
| NVDAx | all | 15.09 | 21.04 | 27.00 | 29.72 | 32.65 | 33.97 | 3834 |
| TSLAx | open | 20.97 | 25.13 | 30.13 | 27.79 | 29.00 | 33.61 | 721 |
| TSLAx | closed | 8.02 | 11.86 | 15.43 | 17.89 | 22.35 | 27.58 | 2899 |
| TSLAx | all | 12.09 | 15.66 | 19.59 | 21.16 | 25.15 | 31.57 | 3647 |
| SPCXx | open | 65.65 | 78.51 | 78.83 | 85.59 | 99.03 | 113.79 | 734 |
| SPCXx | closed | 29.24 | 42.75 | 54.16 | 63.06 | 74.74 | 97.45 | 3285 |
| SPCXx | all | 39.65 | 51.76 | 60.53 | 68.67 | 80.10 | 100.24 | 4042 |

## Realised PnL distribution (primary clip, fire-on-open windows)

Clip **$500**, all symbols, per session × lag. Bps are net of the full cost stack including the flat withdraw fee. Win = realised USD > 0.

### RTH open

| Symbol | N (min) | n | Win% | Mean bps | Med bps | p5 | p25 | p75 | p95 | Worst | Mean USD |
|--------|--------:|--:|-----:|---------:|--------:|---:|----:|----:|----:|------:|---------:|
| CRCLx | 5 | 1 | 100.0% | 22.13 | 22.13 | 22.13 | 22.13 | 22.13 | 22.13 | 22.13 | 1.1064 |
| CRCLx | 10 | 1 | 100.0% | 39.92 | 39.92 | 39.92 | 39.92 | 39.92 | 39.92 | 39.92 | 1.9962 |
| HOODx | 5 | 1 | 100.0% | 33.61 | 33.61 | 33.61 | 33.61 | 33.61 | 33.61 | 33.61 | 1.6807 |
| HOODx | 10 | 1 | 100.0% | 15.10 | 15.10 | 15.10 | 15.10 | 15.10 | 15.10 | 15.10 | 0.7549 |
| HOODx | 15 | 2 | 50.0% | -46.29 | -110.63 | -110.63 | -110.63 | 18.05 | 18.05 | -110.63 | -2.3147 |
| HOODx | 60 | 1 | 0.0% | -122.75 | -122.75 | -122.75 | -122.75 | -122.75 | -122.75 | -122.75 | -6.1373 |
| NVDAx | 5 | 1 | 100.0% | 18.91 | 18.91 | 18.91 | 18.91 | 18.91 | 18.91 | 18.91 | 0.9457 |
| NVDAx | 10 | 1 | 100.0% | 124.21 | 124.21 | 124.21 | 124.21 | 124.21 | 124.21 | 124.21 | 6.2106 |
| NVDAx | 20 | 1 | 100.0% | 50.64 | 50.64 | 50.64 | 50.64 | 50.64 | 50.64 | 50.64 | 2.5321 |
| NVDAx | 30 | 1 | 100.0% | 102.52 | 102.52 | 102.52 | 102.52 | 102.52 | 102.52 | 102.52 | 5.1262 |

Nearest-rank percentiles: at n < 11, p5 often equals worst (small-sample artefact — not a distinct tail estimate).

### Closed session

| Symbol | N (min) | n | Win% | Mean bps | Med bps | p5 | p25 | p75 | p95 | Worst | Mean USD |
|--------|--------:|--:|-----:|---------:|--------:|---:|----:|----:|----:|------:|---------:|
| AAPLx | 5 | 1 | 0.0% | -20.93 | -20.93 | -20.93 | -20.93 | -20.93 | -20.93 | -20.93 | -1.0466 |
| AAPLx | 15 | 2 | 0.0% | -38.70 | -39.39 | -39.39 | -39.39 | -38.00 | -38.00 | -39.39 | -1.9348 |
| AAPLx | 20 | 2 | 0.0% | -42.83 | -47.34 | -47.34 | -47.34 | -38.32 | -38.32 | -47.34 | -2.1416 |
| AAPLx | 30 | 1 | 0.0% | -35.25 | -35.25 | -35.25 | -35.25 | -35.25 | -35.25 | -35.25 | -1.7626 |
| AAPLx | 60 | 2 | 0.0% | -50.12 | -64.98 | -64.98 | -64.98 | -35.25 | -35.25 | -64.98 | -2.5058 |
| CRCLx | 5 | 7 | 42.9% | 41.21 | -19.67 | -32.14 | -21.46 | 92.49 | 152.12 | -32.14 | 2.0603 |
| CRCLx | 10 | 5 | 40.0% | 63.05 | -5.93 | -69.63 | -33.61 | 188.84 | 235.60 | -69.63 | 3.1527 |
| CRCLx | 15 | 5 | 40.0% | 143.68 | -2.72 | -103.31 | -86.06 | 425.33 | 485.17 | -103.31 | 7.1841 |
| CRCLx | 20 | 3 | 66.7% | 141.03 | 0.62 | -132.70 | -132.70 | 555.18 | 555.18 | -132.70 | 7.0517 |
| CRCLx | 30 | 5 | 40.0% | 52.65 | -86.11 | -194.26 | -153.90 | 201.43 | 496.10 | -194.26 | 2.6326 |
| CRCLx | 60 | 4 | 25.0% | -148.89 | -79.24 | -491.75 | -322.08 | -79.24 | 297.50 | -491.75 | -7.4447 |
| GOOGLx | 5 | 2 | 50.0% | 17.71 | -8.63 | -8.63 | -8.63 | 44.04 | 44.04 | -8.63 | 0.8853 |
| GOOGLx | 10 | 2 | 50.0% | 6.98 | -3.62 | -3.62 | -3.62 | 17.59 | 17.59 | -3.62 | 0.3492 |
| GOOGLx | 15 | 2 | 50.0% | -0.38 | -5.11 | -5.11 | -5.11 | 4.36 | 4.36 | -5.11 | -0.0188 |
| GOOGLx | 20 | 3 | 0.0% | -31.19 | -7.32 | -80.65 | -80.65 | -5.59 | -5.59 | -80.65 | -1.5594 |
| GOOGLx | 30 | 3 | 0.0% | -25.28 | -10.52 | -60.34 | -60.34 | -4.96 | -4.96 | -60.34 | -1.2638 |
| HOODx | 5 | 3 | 100.0% | 50.53 | 55.93 | 22.50 | 22.50 | 73.15 | 73.15 | 22.50 | 2.5263 |
| HOODx | 10 | 6 | 83.3% | 33.81 | 35.31 | -28.50 | 18.24 | 65.10 | 73.58 | -28.50 | 1.6906 |
| HOODx | 15 | 5 | 80.0% | 12.38 | 33.68 | -66.49 | 10.78 | 38.51 | 45.42 | -66.49 | 0.6191 |
| HOODx | 20 | 7 | 85.7% | 26.81 | 41.69 | -69.65 | 32.90 | 41.69 | 73.59 | -69.65 | 1.3403 |
| HOODx | 30 | 6 | 83.3% | 14.59 | 39.05 | -134.00 | 24.08 | 54.50 | 60.58 | -134.00 | 0.7297 |
| HOODx | 60 | 5 | 80.0% | 27.38 | 58.71 | -110.81 | 44.97 | 71.48 | 72.55 | -110.81 | 1.3690 |
| METAx | 15 | 1 | 0.0% | -42.99 | -42.99 | -42.99 | -42.99 | -42.99 | -42.99 | -42.99 | -2.1497 |
| METAx | 30 | 1 | 0.0% | -57.84 | -57.84 | -57.84 | -57.84 | -57.84 | -57.84 | -57.84 | -2.8922 |
| NVDAx | 5 | 2 | 0.0% | -20.58 | -20.86 | -20.86 | -20.86 | -20.31 | -20.31 | -20.86 | -1.0291 |
| NVDAx | 10 | 2 | 0.0% | -18.30 | -19.93 | -19.93 | -19.93 | -16.67 | -16.67 | -19.93 | -0.9150 |
| NVDAx | 15 | 1 | 0.0% | -1.88 | -1.88 | -1.88 | -1.88 | -1.88 | -1.88 | -1.88 | -0.0938 |
| NVDAx | 20 | 2 | 0.0% | -6.32 | -10.37 | -10.37 | -10.37 | -2.27 | -2.27 | -10.37 | -0.3161 |
| NVDAx | 30 | 1 | 0.0% | -3.28 | -3.28 | -3.28 | -3.28 | -3.28 | -3.28 | -3.28 | -0.1642 |
| NVDAx | 60 | 1 | 0.0% | -11.03 | -11.03 | -11.03 | -11.03 | -11.03 | -11.03 | -11.03 | -0.5513 |
| TSLAx | 5 | 1 | 0.0% | -23.12 | -23.12 | -23.12 | -23.12 | -23.12 | -23.12 | -23.12 | -1.1559 |
| TSLAx | 10 | 1 | 0.0% | -24.64 | -24.64 | -24.64 | -24.64 | -24.64 | -24.64 | -24.64 | -1.2319 |
| TSLAx | 15 | 2 | 0.0% | -23.42 | -23.73 | -23.73 | -23.73 | -23.12 | -23.12 | -23.73 | -1.1711 |
| TSLAx | 30 | 1 | 0.0% | -23.73 | -23.73 | -23.73 | -23.73 | -23.73 | -23.73 | -23.73 | -1.1863 |
| TSLAx | 60 | 2 | 0.0% | -49.31 | -53.22 | -53.22 | -53.22 | -45.40 | -45.40 | -53.22 | -2.4656 |

## `drift_premium_k` (engine deliverable)

At primary lag **10 min** and clip **$500**, using session-matched σ. Admission: `edge_bps ≥ k · σ_transit`. Report k at 90 / 95 / 99% target realised win rates. Cells show **n/a** when the target is unreachable (too few windows or no k on the 0–5 grid clears it); min_admitted is 5 when n≥5 else 3 (provisional on thin spans).

| Symbol | Session | σ@primary (bps) | k@90% | wr | n | k@95% | wr | n | k@99% | wr | n | Reachable? |
|--------|---------|----------------:|------:|---:|--:|------:|---:|--:|------:|---:|--:|-----------|
| AAPLx | open | 24.85 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| AAPLx | closed | 9.84 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| CRCLx | open | 68.83 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| CRCLx | closed | 58.57 | n/a | 40.0% | 5 | n/a | 40.0% | 5 | n/a | 40.0% | 5 | no |
| GOOGLx | open | 74.27 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| GOOGLx | closed | 22.18 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| HOODx | open | 44.03 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| HOODx | closed | 25.08 | 0.90 | 100.0% | 5 | 0.90 | 100.0% | 5 | 0.90 | 100.0% | 5 | 90/95/99 |
| METAx | open | 22.36 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| METAx | closed | 13.18 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| NVDAx | open | 26.50 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| NVDAx | closed | 14.19 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| SPCXx | open | 78.51 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| SPCXx | closed | 42.75 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| TSLAx | open | 25.13 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| TSLAx | closed | 11.86 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |

**This span derives no open-session `drift_premium_k`.** No open symbol reached a 90% realised win-rate target on the k-grid with enough admitted samples. The bot must not live-trade until a re-run with thicker RTH produces reachable open-session k values (or an explicit owner waiver). Closed-session fits exist for diagnostics only (HOODx k90=0.90 (n=5)) — **do not** use them for RTH admission.

## Clip-size sweep (fee vs Fluxion impact)

Flat withdraw fee = **$1** (20.000 bps on $500, 10.000 bps on $1,000). Primary lag 10 min, RTH open fire-on-open. Optimum = highest mean realised USD / cycle.

| Symbol | Size | n | Mean bps | Med bps | Mean USD | Win% | Fee bps | Mean Flux impact bps | Optimum? |
|--------|-----:|--:|---------:|--------:|---------:|-----:|-------:|---------------------:|---------|
| AAPLx | 100 | 0 | n/a | n/a | n/a | n/a | 100.00 | n/a |  |
| AAPLx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | n/a |  |
| AAPLx | 500 | 0 | n/a | n/a | n/a | n/a | 20.00 | n/a |  |
| AAPLx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | n/a |  |
| CRCLx | 100 | 3 | -138.17 | -144.94 | -1.3817 | 0.0% | 100.00 | 24.24 |  |
| CRCLx | 250 | 1 | -75.20 | -75.20 | -1.8800 | 0.0% | 40.00 | 60.83 |  |
| CRCLx | 500 | 1 | 39.92 | 39.92 | 1.9962 | 100.0% | 20.00 | 120.59 | ✓ |
| CRCLx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | 239.81 |  |
| GOOGLx | 100 | 1 | -163.41 | -163.41 | -1.6341 | 0.0% | 100.00 | 17.04 |  |
| GOOGLx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | n/a |  |
| GOOGLx | 500 | 0 | n/a | n/a | n/a | n/a | 20.00 | n/a |  |
| GOOGLx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | n/a |  |
| HOODx | 100 | 1 | 4.12 | 4.12 | 0.0412 | 100.0% | 100.00 | 17.11 |  |
| HOODx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | 42.55 |  |
| HOODx | 500 | 1 | 15.10 | 15.10 | 0.7549 | 100.0% | 20.00 | 85.08 | ✓ |
| HOODx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | n/a |  |
| METAx | 100 | 0 | n/a | n/a | n/a | n/a | 100.00 | n/a |  |
| METAx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | n/a |  |
| METAx | 500 | 0 | n/a | n/a | n/a | n/a | 20.00 | n/a |  |
| METAx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | n/a |  |
| NVDAx | 100 | 0 | n/a | n/a | n/a | n/a | 100.00 | 17.62 |  |
| NVDAx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | 44.19 |  |
| NVDAx | 500 | 1 | 124.21 | 124.21 | 6.2106 | 100.0% | 20.00 | 88.00 | ✓ |
| NVDAx | 1000 | 1 | 25.88 | 25.88 | 2.5878 | 100.0% | 10.00 | 175.16 |  |
| SPCXx | 100 | 0 | n/a | n/a | n/a | n/a | 100.00 | n/a |  |
| SPCXx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | n/a |  |
| SPCXx | 500 | 0 | n/a | n/a | n/a | n/a | 20.00 | n/a |  |
| SPCXx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | n/a |  |
| TSLAx | 100 | 0 | n/a | n/a | n/a | n/a | 100.00 | n/a |  |
| TSLAx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | n/a |  |
| TSLAx | 500 | 0 | n/a | n/a | n/a | n/a | 20.00 | n/a |  |
| TSLAx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | n/a |  |

### Stated optimum per symbol (RTH open, primary lag)

| Symbol | Optimum size | n | Mean USD / cycle | Mean bps | Win% | Note |
|--------|-------------:|--:|-----------------:|---------:|-----:|------|
| AAPLx | $n/a | 0 | n/a | n/a | n/a | no positive-mean size |
| CRCLx | $500 | 1 | 1.9962 | 39.92 | 100.0% | provisional (n<5) |
| GOOGLx | $n/a | 0 | n/a | n/a | n/a | no positive-mean size |
| HOODx | $500 | 1 | 0.7549 | 15.10 | 100.0% | provisional (n<5) |
| METAx | $n/a | 0 | n/a | n/a | n/a | no positive-mean size |
| NVDAx | $500 | 1 | 6.2106 | 124.21 | 100.0% | provisional (n<5) |
| SPCXx | $n/a | 0 | n/a | n/a | n/a | no positive-mean size |
| TSLAx | $n/a | 0 | n/a | n/a | n/a | no positive-mean size |

### Closed-session optimum (diagnostic only)

Not used for live RTH sizing. Surfaces any ≥n sizes when open-session windows are too sparse.

| Symbol | Optimum size | n | Mean USD / cycle | Mean bps | Win% | Note |
|--------|-------------:|--:|-----------------:|---------:|-----:|------|
| AAPLx | $n/a | 0 | n/a | n/a | n/a | no positive-mean size |
| CRCLx | $1000 | 1 | 15.3463 | 153.46 | 100.0% | provisional (n<5); closed diagnostic |
| GOOGLx | $500 | 2 | 0.3492 | 6.98 | 50.0% | provisional (n<5); closed diagnostic |
| HOODx | $500 | 6 | 1.6906 | 33.81 | 83.3% | closed diagnostic |
| METAx | $n/a | 0 | n/a | n/a | n/a | no positive-mean size |
| NVDAx | $n/a | 0 | n/a | n/a | n/a | no positive-mean size |
| SPCXx | $n/a | 0 | n/a | n/a | n/a | no positive-mean size |
| TSLAx | $n/a | 0 | n/a | n/a | n/a | no positive-mean size |

## Symbol universe under sequential model

Per-symbol series-level capturable profit/day at primary lag + primary clip, single-flight with 1-day re-entry cooldown (one trade per window day). Sequential books **realised** PnL; simultaneous books paper simultaneous PnL **without** the flat withdraw fee (M0 parity).

| Symbol | Session | Sim $/day | Seq $/day | Seq win% | Windows | Seq ≥1 $/day? |
|--------|---------|----------:|----------:|---------:|--------:|--------------|
| CRCLx | open | 0.1073 | 0.9913 | 100.0% | 1 | no |
| CRCLx | closed | 2.8378 | -0.8345 | 40.0% | 5 | no |
| GOOGLx | closed | 0.8168 | -0.0900 | 50.0% | 2 | no |
| HOODx | open | 0.0772 | 0.3749 | 100.0% | 1 | no |
| HOODx | closed | 8.8881 | 0.4528 | 83.3% | 6 | no |
| NVDAx | open | 0.5541 | 3.0842 | 100.0% | 1 | yes |
| NVDAx | closed | 0.0218 | -0.4949 | 0.0% | 2 | no |
| TSLAx | closed | 0.0207 | -0.6118 | 0.0% | 1 | no |

Sequential vs simultaneous on this span: simultaneous portfolio 0.7387 → sequential 4.4503 USDT/day (ratio 6.025). Sequential books **all** admitted cycles' realised PnL (losses included) with flight = lag (10 min); simultaneous books decision-time paper edge with a short flight. Direction 1 only, primary clip $500, fire-on-open windows — not the M0 two-direction $1k headline. Compare methodology carefully against `m8-xstocks-edge-quant.md`.

## Sensitivity: N = 30 and 60 minutes

Deposit-timeout evidence base. Same primary clip, RTH open portfolio single-flight realised $/day and mean realised bps.

| Lag (min) | $/day (all) | Win% (all) | Mean bps | n | $/day (paired w/ primary) | Win% (paired) | n_paired |
|----------:|-----------:|-----------:|---------:|--:|-------------------------:|--------------:|---------:|
| 5 | 1.8537 | 100.0% | 24.88 | 3 | 1.8537 | 100.0% | 3 |
| 10 | 4.4503 | 100.0% | 59.74 | 3 | 4.4503 | 100.0% | 3 |
| 15 | 0.4481 | 50.0% | -46.29 | 2 | 0.4481 | 100.0% | 1 |
| 20 | 1.2574 | 100.0% | 50.64 | 1 | 1.2574 | 100.0% | 1 |
| 30 | 2.5456 | 100.0% | 102.52 | 1 | 2.5456 | 100.0% | 1 |
| 60 | -3.0478 | 0.0% | -122.75 | 1 | -3.0478 | 0.0% | 1 |

Each lag reports (a) **all** open windows that resolve at that lag (n may shrink — missing delayed books) and (b) the **paired** subset also present at the primary lag N=10 (fair degradation). Portfolio flight = lag. If N=30/60 paired $/day collapses, set `deposit_timeout_s` inside the still-viable horizon (bot DESIGN §2.8).

## Fit quality & caveats

- Study window 2026-08-05 03:19:03 UTC → 2026-08-07 03:38:46 UTC (2.014 calendar days). RTH hours in window: 13.00 h (excl. collector_down: 5.37 h). Closed: 35.33 h (excl. gap: 24.21 h). Collector downtime: 40.31 h.
- Flat withdraw fee assumed **$1** per cycle (bot DESIGN §2.3 / §3.1 A+D). Authenticated fee pull (WHI-907) should replace this constant when it lands.
- Bybit sell at t+N is a paper VWAP with the same depth model as PnL v2 — not a live fill. Thin books and crediting delays can be worse than measured.
- Direction 2 (`buy_bybit_sell_fluxion`) is out of scope (needs ~15 min Bybit xStock withdrawal before the DEX leg).
- No corporate-action calendar applied; re-runs over longer spans must disclose excluded days.
- Fit quality is limited by raw retention (~2d book/depth). Re-run after ≥5 clean RTH sessions before locking production k.

## What the bot should consume

- Admission: `edge_bps ≥ min_edge_bps[session][direction]` **and** `edge_bps ≥ drift_premium_k[symbol] × sigma_transit_bps[symbol, N]` with N≈10 min — **but this span produced no open-session k**; block live trading until a re-run supplies one.
- Clip size: per-symbol optimum from the sweep (often below $1000); flat fee pushes up, Fluxion impact + transit premium push down.
- Symbol universe: open-session sequential go-list above; drop symbols whose sequential $/day is ~0 even before the premium.
- Deposit timeout: set from the lag-sensitivity table so that waiting inside the timeout remains positive-EV on average; beyond that, take the exposure-breaker path.
- Update bot `docs/DESIGN.md` §8 delay-decay bullet to cite this report (companion commit in mantle-stocks-arbitrage-bots).

---

*Companion machine-readable payload: `docs/references/m8-delay-decay.json` (schema version 1).*
