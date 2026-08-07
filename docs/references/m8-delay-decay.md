# Delay-decay: sequential transfer cycle edge (WHI-915 / M8)

Does the bot's **zero-inventory transfer cycle** still earn money when the legs are sequential? Fluxion buy at opportunity-window open, Bybit sell at `t + N`, full cost stack including the flat recycle withdraw fee. Companion to the simultaneous M0 report (`docs/references/m8-xstocks-edge-quant.md`).

**Generated:** 2026-08-07 03:23:49 UTC

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
| pricing_anomaly_gate | 500 |

### Cycle reconstruction

1. Detect opportunity windows on **simultaneous** net edge (`buy_fluxion_sell_bybit` only) at each clip size; fire on window open.
2. At entry: executable Fluxion buy (fee + impact) and same-timestamp Bybit sell (baseline simultaneous PnL).
3. At `t + N`: Bybit sell VWAP for the **same base qty**, fee-inclusive.
4. Realised PnL = delayed Bybit proceeds − Fluxion spend − gas − USDT/USDC basis − flat withdraw fee.
5. Transit σ: sample std of Bybit mid log-returns over each lag (session-split).
6. `drift_premium_k`: minimal k on a 0.1 grid such that `simultaneous_edge ≥ k · σ` yields the target realised win rate (min 5 admitted samples).

## Data span

Study window wall clock: **2026-08-05 03:19:03 UTC → 2026-08-07 03:22:56 UTC** (2.003 d). RTH excl. gap: **5.37 h**. Delayed sells may resolve up to 60 min past the book max timestamp.

| Table | Rows | Min (UTC) | Max (UTC) |
|-------|-----:|-----------|-----------|
| `bybit_book` | 996,438 | 2026-08-05 03:19:03 UTC | 2026-08-07 03:22:56 UTC |
| `bybit_depth` | 749,668 | 2026-08-05 03:19:03 UTC | 2026-08-07 03:22:56 UTC |
| `fluxion_pool_state` | 895,488 | 2026-08-02 03:18:24 UTC | 2026-08-07 03:22:56 UTC |
| `collector_gaps` | 3,735 | 2026-08-02 03:18:32 UTC | 2026-08-07 02:51:08 UTC |

## Headline (sequential vs simultaneous)

| Metric | Value |
|--------|------:|
| Study calendar days | 2.003 |
| Primary lag | 10 min |
| Primary clip | $500 |
| Withdraw fee (flat) | $1 / cycle |
| Simultaneous portfolio $/day (baseline, no withdraw fee) | 0.7427 |
| Sequential portfolio $/day (realised, w/ withdraw fee) | 4.4748 |
| Sequential / simultaneous ratio | 6.025 |
| Sequential go-list (open, primary) | CRCLx, HOODx, NVDAx |
| Symbols clearing ≥1 USDT/day sequential | NVDAx |
| **Verdict** | **NO-GO (sequential)** |

Sequential portfolio **4.4748 USDT/day** is below the 5 no-go floor. Simultaneous baseline was 0.7427 USDT/day. Do not live-trade on this span.

## Measured transit-window σ (Bybit mid log-return, bps)

Replaces the ~40 bps / 10 min HOODx estimate. 1σ sample std; RTH and closed reported separately.

| Symbol | Session | N=5m | N=10m | N=15m | N=20m | N=30m | N=60m | n@10m |
|--------|---------|-----:|------:|------:|------:|------:|------:|------:|
| AAPLx | open | 15.94 | 23.58 | 31.12 | 35.82 | 46.87 | 58.31 | 526 |
| AAPLx | closed | 6.55 | 9.90 | 11.99 | 13.73 | 15.87 | 19.86 | 1635 |
| AAPLx | all | 9.87 | 15.69 | 20.05 | 22.44 | 26.61 | 31.71 | 2174 |
| CRCLx | open | 51.63 | 66.08 | 77.71 | 81.09 | 77.65 | 100.85 | 434 |
| CRCLx | closed | 33.56 | 53.42 | 69.91 | 88.34 | 107.70 | 130.19 | 2153 |
| CRCLx | all | 38.81 | 57.64 | 72.11 | 87.28 | 103.52 | 126.17 | 2595 |
| GOOGLx | open | 34.78 | 63.49 | 61.21 | 70.86 | 90.88 | 121.53 | 493 |
| GOOGLx | closed | 17.53 | 23.01 | 23.75 | 24.41 | 28.58 | 33.18 | 1711 |
| GOOGLx | all | 22.62 | 36.70 | 36.27 | 41.21 | 49.33 | 61.61 | 2221 |
| HOODx | open | 28.59 | 38.72 | 52.11 | 58.18 | 51.56 | 48.68 | 464 |
| HOODx | closed | 16.61 | 24.39 | 28.08 | 33.11 | 37.24 | 36.87 | 1532 |
| HOODx | all | 20.65 | 28.75 | 35.02 | 40.92 | 44.12 | 44.00 | 2012 |
| METAx | open | 18.04 | 22.19 | 26.30 | 27.97 | 30.86 | 51.18 | 510 |
| METAx | closed | 10.08 | 12.17 | 16.16 | 19.08 | 23.23 | 31.14 | 1450 |
| METAx | all | 13.45 | 17.61 | 21.09 | 23.71 | 28.36 | 42.44 | 1972 |
| NVDAx | open | 23.98 | 25.98 | 28.04 | 28.01 | 31.76 | 40.40 | 444 |
| NVDAx | closed | 9.46 | 13.58 | 17.08 | 20.87 | 25.45 | 32.40 | 2285 |
| NVDAx | all | 14.09 | 18.77 | 23.97 | 26.07 | 29.98 | 34.14 | 2739 |
| TSLAx | open | 21.28 | 24.55 | 30.15 | 30.63 | 30.46 | 33.17 | 453 |
| TSLAx | closed | 8.00 | 11.73 | 15.05 | 17.40 | 21.54 | 27.45 | 2104 |
| TSLAx | all | 11.65 | 14.91 | 18.79 | 20.83 | 24.59 | 30.76 | 2565 |
| SPCXx | open | 62.32 | 72.32 | 76.62 | 82.57 | 100.66 | 109.84 | 453 |
| SPCXx | closed | 26.21 | 37.67 | 48.80 | 58.74 | 69.36 | 87.34 | 2168 |
| SPCXx | all | 35.59 | 45.96 | 54.76 | 63.85 | 75.08 | 90.95 | 2634 |

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
| AAPLx | open | 23.58 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| AAPLx | closed | 9.90 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| CRCLx | open | 66.08 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| CRCLx | closed | 53.42 | n/a | 40.0% | 5 | n/a | 40.0% | 5 | n/a | 40.0% | 5 | no |
| GOOGLx | open | 63.49 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| GOOGLx | closed | 23.01 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| HOODx | open | 38.72 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| HOODx | closed | 24.39 | 0.90 | 100.0% | 5 | 0.90 | 100.0% | 5 | 0.90 | 100.0% | 5 | 90/95/99 |
| METAx | open | 22.19 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| METAx | closed | 12.17 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| NVDAx | open | 25.98 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| NVDAx | closed | 13.58 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| SPCXx | open | 72.32 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| SPCXx | closed | 37.67 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| TSLAx | open | 24.55 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |
| TSLAx | closed | 11.73 | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 | no |

No open-session symbol reached a 90% realised win-rate target on the k-grid with ≥5 admitted samples. Either widen the span, lower the clip, or treat sequential trading as not yet admissible.

## Clip-size sweep (fee vs Fluxion impact)

Flat withdraw fee = **$1** (20.000 bps on $500, 10.000 bps on $1,000). Primary lag 10 min, RTH open fire-on-open. Optimum = highest mean realised USD / cycle.

| Symbol | Size | n | Mean bps | Med bps | Mean USD | Win% | Fee bps | Mean Flux impact bps | Optimum? |
|--------|-----:|--:|---------:|--------:|---------:|-----:|-------:|---------------------:|---------|
| AAPLx | 100 | 0 | n/a | n/a | n/a | n/a | 100.00 | 17.53 |  |
| AAPLx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | 43.90 |  |
| AAPLx | 500 | 0 | n/a | n/a | n/a | n/a | 20.00 | 87.57 |  |
| AAPLx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | n/a |  |
| CRCLx | 100 | 3 | -138.17 | -144.94 | -1.3817 | 0.0% | 100.00 | 24.46 |  |
| CRCLx | 250 | 1 | -75.20 | -75.20 | -1.8800 | 0.0% | 40.00 | 60.74 |  |
| CRCLx | 500 | 1 | 39.92 | 39.92 | 1.9962 | 100.0% | 20.00 | 119.35 | ✓ |
| CRCLx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | 236.49 |  |
| GOOGLx | 100 | 1 | -163.41 | -163.41 | -1.6341 | 0.0% | 100.00 | 17.06 |  |
| GOOGLx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | 42.61 |  |
| GOOGLx | 500 | 0 | n/a | n/a | n/a | n/a | 20.00 | 84.70 |  |
| GOOGLx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | 168.50 |  |
| HOODx | 100 | 1 | 4.12 | 4.12 | 0.0412 | 100.0% | 100.00 | 17.02 |  |
| HOODx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | 42.38 |  |
| HOODx | 500 | 1 | 15.10 | 15.10 | 0.7549 | 100.0% | 20.00 | 84.84 | ✓ |
| HOODx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | 169.60 |  |
| METAx | 100 | 0 | n/a | n/a | n/a | n/a | 100.00 | 18.91 |  |
| METAx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | 47.11 |  |
| METAx | 500 | 0 | n/a | n/a | n/a | n/a | 20.00 | 94.73 |  |
| METAx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | n/a |  |
| NVDAx | 100 | 0 | n/a | n/a | n/a | n/a | 100.00 | 17.70 |  |
| NVDAx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | 44.15 |  |
| NVDAx | 500 | 1 | 124.21 | 124.21 | 6.2106 | 100.0% | 20.00 | 88.40 | ✓ |
| NVDAx | 1000 | 1 | 25.88 | 25.88 | 2.5878 | 100.0% | 10.00 | 175.16 |  |
| SPCXx | 100 | 0 | n/a | n/a | n/a | n/a | 100.00 | n/a |  |
| SPCXx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | n/a |  |
| SPCXx | 500 | 0 | n/a | n/a | n/a | n/a | 20.00 | n/a |  |
| SPCXx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | n/a |  |
| TSLAx | 100 | 0 | n/a | n/a | n/a | n/a | 100.00 | 20.59 |  |
| TSLAx | 250 | 0 | n/a | n/a | n/a | n/a | 40.00 | 51.52 |  |
| TSLAx | 500 | 0 | n/a | n/a | n/a | n/a | 20.00 | 102.59 |  |
| TSLAx | 1000 | 0 | n/a | n/a | n/a | n/a | 10.00 | n/a |  |

### Stated optimum per symbol (RTH open, primary lag)

| Symbol | Optimum size | Mean USD / cycle | Mean bps | Win% |
|--------|-------------:|-----------------:|---------:|-----:|
| AAPLx | $n/a | n/a | n/a | n/a |
| CRCLx | $500 | 1.9962 | 39.92 | 100.0% |
| GOOGLx | $n/a | n/a | n/a | n/a |
| HOODx | $500 | 0.7549 | 15.10 | 100.0% |
| METAx | $n/a | n/a | n/a | n/a |
| NVDAx | $500 | 6.2106 | 124.21 | 100.0% |
| SPCXx | $n/a | n/a | n/a | n/a |
| TSLAx | $n/a | n/a | n/a | n/a |

## Symbol universe under sequential model

Per-symbol series-level capturable profit/day at primary lag + primary clip, single-flight with 1-day re-entry cooldown (one trade per window day). Sequential books **realised** PnL; simultaneous books paper simultaneous PnL **without** the flat withdraw fee (M0 parity).

| Symbol | Session | Sim $/day | Seq $/day | Seq win% | Windows | Seq ≥1 $/day? |
|--------|---------|----------:|----------:|---------:|--------:|--------------|
| CRCLx | open | 0.1079 | 0.9967 | 100.0% | 1 | no |
| CRCLx | closed | 2.8534 | -0.8390 | 40.0% | 5 | no |
| GOOGLx | closed | 0.8212 | -0.0904 | 50.0% | 2 | no |
| HOODx | open | 0.0777 | 0.3769 | 100.0% | 1 | no |
| HOODx | closed | 8.9370 | 0.4553 | 83.3% | 6 | no |
| NVDAx | open | 0.5572 | 3.1011 | 100.0% | 1 | yes |
| NVDAx | closed | 0.0220 | -0.4976 | 0.0% | 2 | no |
| TSLAx | closed | 0.0208 | -0.6151 | 0.0% | 1 | no |

Sequential vs simultaneous on this span: simultaneous portfolio 0.7427 → sequential 4.4748 USDT/day (ratio 6.025). Sequential books **all** admitted cycles' realised PnL (losses included) with flight = lag (10 min); simultaneous books decision-time paper edge with a short flight. Direction 1 only, primary clip $500, fire-on-open windows — not the M0 two-direction $1k headline. Compare methodology carefully against `m8-xstocks-edge-quant.md`.

## Sensitivity: N = 30 and 60 minutes

Deposit-timeout evidence base. Same primary clip, RTH open portfolio single-flight realised $/day and mean realised bps.

| Lag (min) | $/day (all) | Win% (all) | Mean bps | n | $/day (paired w/ primary) | Win% (paired) | n_paired |
|----------:|-----------:|-----------:|---------:|--:|-------------------------:|--------------:|---------:|
| 5 | 1.8638 | 100.0% | 24.88 | 3 | 1.8638 | 100.0% | 3 |
| 10 | 4.4748 | 100.0% | 59.74 | 3 | 4.4748 | 100.0% | 3 |
| 15 | 0.4506 | 50.0% | -46.29 | 2 | 0.4506 | 100.0% | 1 |
| 20 | 1.2643 | 100.0% | 50.64 | 1 | 1.2643 | 100.0% | 1 |
| 30 | 2.5596 | 100.0% | 102.52 | 1 | 2.5596 | 100.0% | 1 |
| 60 | -3.0645 | 0.0% | -122.75 | 1 | -3.0645 | 0.0% | 1 |

Each lag reports (a) **all** open windows that resolve at that lag (n may shrink — missing delayed books) and (b) the **paired** subset also present at the primary lag N=10 (fair degradation). Portfolio flight = lag. If N=30/60 paired $/day collapses, set `deposit_timeout_s` inside the still-viable horizon (bot DESIGN §2.8).

## Fit quality & caveats

- Study window 2026-08-05 03:19:03 UTC → 2026-08-07 03:22:56 UTC (2.003 calendar days). RTH hours in window: 13.00 h (excl. collector_down: 5.37 h). Closed: 35.06 h (excl. gap: 23.95 h). Collector downtime: 40.31 h.
- Flat withdraw fee assumed **$1** per cycle (bot DESIGN §2.3 / §3.1 A+D). Authenticated fee pull (WHI-907) should replace this constant when it lands.
- Bybit sell at t+N is a paper VWAP with the same depth model as PnL v2 — not a live fill. Thin books and crediting delays can be worse than measured.
- Direction 2 (`buy_bybit_sell_fluxion`) is out of scope (needs ~15 min Bybit xStock withdrawal before the DEX leg).
- No corporate-action calendar applied; re-runs over longer spans must disclose excluded days.
- Fit quality is limited by raw retention (~2d book/depth). Re-run after ≥5 clean RTH sessions before locking production k.

## What the bot should consume

- Admission: `edge_bps ≥ min_edge_bps[session][direction]` **and** `edge_bps ≥ drift_premium_k[symbol] × sigma_transit_bps[symbol, N]` with N≈10 min until deposit latency is measured live.
- Clip size: per-symbol optimum from the sweep (often below $1000); flat fee pushes up, Fluxion impact + transit premium push down.
- Symbol universe: open-session sequential go-list above; drop symbols whose sequential $/day is ~0 even before the premium.
- Deposit timeout: set from the lag-sensitivity table so that waiting inside the timeout remains positive-EV on average; beyond that, take the exposure-breaker path.
- Update bot `docs/DESIGN.md` §8 delay-decay bullet to cite this report (companion commit in mantle-stocks-arbitrage-bots).

---

*Companion machine-readable payload: `docs/references/m8-delay-decay.json` (schema version 1).*
