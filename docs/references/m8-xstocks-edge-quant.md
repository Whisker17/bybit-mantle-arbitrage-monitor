# xStocks arb edge quantification & threshold fit (WHI-909 / M8)

Go/no-go report for the sibling execution project `mantle-stocks-arbitrage-bots` under the **corrected cost stack** (WHI-909). Headline numbers are **AMM-only** (bot v1); RFQ is reported separately as a v2 candidate.

**Generated:** 2026-08-08 15:09:32 UTC

> **Supersedes WHI-866.** Prior report retained at `docs/references/m8-xstocks-edge-quant-v1-whi866.md` (10 bps taker, 0 basis, 0 rebalance, 0..60 sweep). This re-run uses live USDCUSDT premium, 20 bps Adventure Zone taker (primary), rebalance amortization, and an extended 0..200 bps threshold sweep.

> **Caveat (WHI-1042):** primary stack in this note used **taker 20** (then-config). Live bybit-fluxion is **15 bps** taker as of 2026-08-11. Headline M0 numbers are ~5 bps tight vs live; do not rewrite the historical verdict in place — re-run quant if a fresh go/no-go is needed.

## Regeneration

```bash
uv run python scripts/xstocks_edge_quant.py --db data/monitor-bybit-fluxion.db --sample-ms 20000
```

Pure helpers: `monitor.analysis.edge_quant` + `monitor.analysis.cost_stack` (unit-tested).
Venue math: `monitor.metrics.pnl_v2.compute_pnl_usd` (not reimplemented).

## Collector health (gate before headlines)

Downtime inventory since WHI-835 land (2026-08-04 02:35:00 UTC): **34** `collector_down` intervals, **30.85 h** total wall-clock gap.

| Day (UTC) | Total gap (h) | RTH (h) | RTH lost (h) | RTH clean (h) |
|-----------|-------------:|--------:|-------------:|--------------:|
| 2026-08-04 | 10.78 | 6.50 | 5.13 | 1.37 |
| 2026-08-05 | 9.92 | 6.50 | 4.23 | 2.27 |
| 2026-08-06 | 9.13 | 6.50 | 3.40 | 3.10 |
| 2026-08-07 | 0.77 | 6.50 | 0.00 | 6.50 |
| 2026-08-08 | 0.30 | 0.00 | 0.00 | 0.00 |

**Study window clean RTH (excl. collector_down):** **9.37 h** (wall RTH 11.38 h; RTH lost to gaps 2.02 h).

> **Coverage note:** 1 study day(s) lost >1 h RTH to `collector_down` (2026-08-06). Effective clean RTH in the study window is still **9.37 h** (improved vs WHI-866's 5.69 h). Headlines follow; treat thresholds as provisional if concentration is high.

## Decision rule (bot DESIGN §1.4)

After **all** corrected costs (Bybit taker + live USDT/USDC basis + rebalance amortization + Fluxion pool fee + bilateral slip + gas + withdrawal fees when wired):

- **Go:** average capturable profit ≥ **15 USDT/day** at 5000 USDT inventory with ≤ **1000 USDT** per trade, **and** ≥ **3 symbols** with stable windows.
- **No-go:** < **5 USDT/day**.
- In between: judge on window time-distribution (open-auction-only clusters discount heavily).
- **Both legs required.** A dollar figure alone that clears ≥15 while stable-symbol count is <3 is still **not** a go.

## Method

| Item | Value |
|------|--------|
| market | bybit-fluxion |
| engine | monitor.metrics.pnl_v2.compute_pnl_usd |
| analysis | monitor.analysis.edge_quant + cost_stack |
| notionals_usd | 500, 1000 |
| sample_ms | 20000 |
| align_ms | 15000 |
| reentry_cooldown_ms | 86400000 |
| trade_duration_ms | 5000 |
| max_gap_ms | 120000 |
| threshold_sweep_bps | 0..60 step 2, then ..200 step 5 |
| capture_fraction | 0.70 |
| inventory_usd | 5000 |
| max_trade_usd | 1000 |
| pricing_anomaly_gate | 300 |
| bybit_taker_fee_bps_primary | 20 |
| rebalance_amortized_bps_primary | 1 |
| basis | live USDCUSDT 1m mid → premium bps (as-of join) |

### Cost stack (WHI-909)

- **Bybit taker:** primary **20 bps** (Adventure Zone, measured WHI-959 / bot fee-rate pull). Sensitivity also reported at 10, 20 bps.
- **USDT/USDC basis:** live per-timestamp USDCUSDT 1m mid → `usdc_premium_bps = (mid − 1) × 1e4`. Source: `bybit_rest_kline_1m` (n=2883, median 6.50000 bps, range [3.50000, 8.50000] bps). **Sign:** paying USDC (`buy_fluxion_sell_bybit`) is charged the premium; receiving USDC is credited. Not a hardcoded 7.5.
- **Rebalance amortization:** primary **1 bps** on skew-building direction only (`buy_fluxion_sell_bybit`). Model: (fixed withdraw+gas + variable conversion bps) / batch notional. Sensitivity at 1, 5, 13.5 bps (1 ≈ USDT0-withdraw+Agni; 11–13.5 ≈ Bybit spot conversion path).
- **Fluxion pool fee 30 bps:** already inside fee-inclusive AMM quotes (unchanged; not double-counted).

### Capturable-profit model

- **Single-flight:** at most one trade in flight (series-level for per-symbol tables; portfolio-level for the go/no-go headline).
- **Per-trade cap:** ≤ 1000 USDT (bot max). Analysis rungs: $500 and $1,000.
- **Re-entry cooldown:** 86400000 ms after trade_duration=5000 ms. Default is one calendar day so the headline is **one trade per window** (fire-on-open PnL). Pass a shorter `--reentry-cooldown-ms` for a multi-entry sensitivity.
- **Threshold fit:** highest `min_edge_bps` on the extended 0..60 step 2, then ..200 step 5 sweep that still retains ≥ 70% of zero-threshold capturable profit (knee).
- **AMM vs RFQ:** separate columns; RFQ sizes are poll-native (scaled down only when poll notional > $1,000). No AMM impact curve is interpolated onto RFQ rows.

## Data span inventory

### Journal tables used

Retention (`config/collector.yaml` → `retention`) prunes raw Bybit L1 at ~2 days and depth at ~2 days; pool state ~7 days; RFQ polls ~3 days; `bybit_book_1m` downsample ~14 days. **This study uses raw aligned ticks only** (depth-aware PnL v2) — not the 1m bars — because capturable profit at $500/$1,000 requires the depth curve and pool geometry, not L1 alone.

| Table | Rows | Min (UTC) | Max (UTC) | Role |
|-------|-----:|-----------|-----------|------|
| `bybit_book` | 1,108,173 | 2026-08-06 15:07:00 UTC | 2026-08-08 15:08:14 UTC | CEX L1 (primary timeline; ~2d raw retention) |
| `bybit_depth` | 871,817 | 2026-08-06 15:07:50 UTC | 2026-08-08 15:08:14 UTC | CEX VWAP curve for $500/$1k slip (~2d retention) |
| `fluxion_pool_state` | 1,294,592 | 2026-08-02 03:18:24 UTC | 2026-08-08 15:08:14 UTC | AMM geometry (as-of join; ~7d retention) |
| `fluxion_rfq_quotes` | 81,898 | 2026-08-05 15:07:51 UTC | 2026-08-08 15:08:14 UTC | RFQ polls (separate column; ~3d retention) |
| `bybit_book_1m` | 39,994 | 2026-08-02 03:18:00 UTC | 2026-08-06 15:06:00 UTC | Survives longer (~14d) but **not used** (no depth) |
| `collector_gaps` | 5,445 | 2026-08-02 03:18:32 UTC | 2026-08-08 15:07:25 UTC | Downtime exclusion (`source=collector_down`) |
| `USDCUSDT klines (external)` | 2,883 | 2026-08-06 15:06:00 UTC | 2026-08-08 15:08:00 UTC | Live basis series (Bybit public 1m; as-of join) |

### Per-symbol coverage (raw `bybit_book`)

Study window wall clock: **2026-08-06 15:07:00 UTC → 2026-08-08 15:08:14 UTC** (48.0 h). RTH hours in window: **11.38 h** (excl. collector_down: **9.37 h**). Closed hours: **36.64 h** (excl. gap: **31.57 h**).

**Collector downtime:** 5 `collector_down` intervals totaling **7.07 h** (of which **2.02 h** fell inside RTH). Samples inside those intervals are excluded.

| Pair | Book rows | First | Last | AMM pool? | Notes |
|------|----------:|-------|------|-----------|-------|
| AAPLx | 106,986 | 2026-08-06 15:07:00 UTC | 2026-08-08 15:05:22 UTC | yes | — |
| AMZNx | 46,347 | 2026-08-06 15:07:05 UTC | 2026-08-08 15:08:02 UTC | no | CEX-only (no Fluxion pool) |
| COINx | 72,307 | 2026-08-06 15:07:00 UTC | 2026-08-08 15:08:06 UTC | no | CEX-only (no Fluxion pool) |
| CRCLx | 86,008 | 2026-08-06 15:07:00 UTC | 2026-08-08 15:07:44 UTC | yes | — |
| GOOGLx | 45,284 | 2026-08-06 15:07:04 UTC | 2026-08-08 15:07:58 UTC | yes | — |
| HOODx | 49,941 | 2026-08-06 15:07:00 UTC | 2026-08-08 15:07:40 UTC | yes | — |
| MCDx | 68,699 | 2026-08-06 15:07:02 UTC | 2026-08-08 15:08:14 UTC | no | CEX-only (no Fluxion pool) |
| METAx | 93,809 | 2026-08-06 15:07:00 UTC | 2026-08-08 15:08:12 UTC | yes | — |
| NVDAx | 106,283 | 2026-08-06 15:07:00 UTC | 2026-08-08 15:08:14 UTC | yes | — |
| SPCXx | 213,638 | 2026-08-06 15:07:00 UTC | 2026-08-08 15:08:13 UTC | yes | frequent pricing_anomaly / thin pool — often excluded by gate |
| TSLAx | 218,871 | 2026-08-06 15:07:00 UTC | 2026-08-08 15:08:13 UTC | yes | — |

## Hygiene / exclusions

- No automated corporate-action calendar is applied. The observation window is 2026-08-06 15:07:00 UTC → 2026-08-08 15:08:14 UTC; re-runs over a longer span must re-check dividends/splits/rebases and disclose any excluded days (bot DESIGN §8).
- No bStocks-style share rebase segment break was introduced for Fluxion xStocks wrappers in this study.
- USDCUSDT 1m mid is HL2 of the bar starting at or before the sample (≤60s of intra-bar look-ahead). Samples without a basis bar within 120000 ms are skipped (no constant fallback).
- Samples with `gap=1` on book/pool/depth rows are dropped at load.
- Pairs failing `amm_quote_for_cex` (empty_pool / invalid_mid / pricing_anomaly, default |spread| > 300 bps) are excluded from AMM samples for that timestamp (SPCXx frequently hits this).
- CEX-only inventory pairs (no Fluxion AMM) are out of scope for the bot v1 AMM path and appear only in the coverage table.
- Headline portfolio mixes open+closed AMM $1000 windows at T=0 (one trade per window, single-flight). Go-list symbols are open-session fits only.
- Basis is live per-timestamp USDCUSDT premium (not the panel constant 7.5); rebalance amortization charged only on buy_fluxion_sell_bybit.

## Headline go/no-go (AMM-only, portfolio single-flight)

Primary cost stack: **taker 20 bps** (believed correct) + **live basis** + **rebalance 1 bps** on skew-building direction. Clean RTH before this table: **9.37 h**.

| Metric | Value |
|--------|------:|
| Study calendar days (span/86400s) | 2.001 |
| Portfolio capturable profit (total, $1000 rung) | 1.5459 USDT |
| **Average capturable profit / day** | **0.7726 USDT/day** |
| §1.4 $/day gate (≥15) | FAIL |
| Symbols with fitted stable windows (open, $1000) | 2 |
| §1.4 ≥3-symbol gate | FAIL |
| Go-list | CRCLx, GOOGLx |
| Core go-list (≥1 USDT/day open fit) | — |
| Portfolio $/day excluding HOODx | 0.7726 |
| **Verdict (both legs)** | **NO-GO** |

Portfolio average **0.7726 USDT/day** is below the 5 USDT/day no-go floor. Stable symbols: 2 (need ≥3). Do not size beyond a minimum proving stage.

## Cost-stack sensitivity matrix

Portfolio AMM $1000 single-flight $/day and stable open-session symbol count under each (taker × rebalance) cell. Live basis in every cell. **Bold** = primary (20 / 1).

| Taker bps | Rebalance bps | $/day | Stable symbols | $/day gate | Symbol gate | Verdict |
|----------:|--------------:|------:|---------------:|:----------:|:-----------:|---------|
| 10 | 1 | 3.4639 | 3 | FAIL | PASS | NO-GO |
| 10 | 5 | 2.4226 | 2 | FAIL | FAIL | NO-GO |
| 10 | 13.5 | 0.6925 | 2 | FAIL | FAIL | NO-GO |
| **20** | **1** | **0.7726** | 2 | FAIL | FAIL | NO-GO |
| 20 | 5 | 0.2956 | 2 | FAIL | FAIL | NO-GO |
| 20 | 13.5 | 0.2864 | 1 | FAIL | FAIL | NO-GO |

## Per-symbol survival (corrected primary stack)

Which symbols still clear positive open-session capturable profit at the primary stack (taker 20, rebalance 1, live basis) on the $500 and $1,000 rungs. Fit bps is the knee at $1,000 for the best direction.

| Symbol | Survive $500? | $500 $/day | Survive $1k? | $1k $/day | Fit bps | Direction |
|--------|:-------------:|-----------:|:------------:|-----------:|--------:|-----------|
| AAPLx | yes | 0.5446 | no | 0 | 0 | buy_bybit_sell_fluxion |
| CRCLx | yes | 7.9296 | yes | 0.4862 | 2 | buy_fluxion_sell_bybit |
| GOOGLx | yes | 3.0632 | yes | 0.2864 | 0 | buy_bybit_sell_fluxion |
| HOODx | yes | 2.1196 | no | 0 | 0 | buy_bybit_sell_fluxion |
| METAx | yes | 0.3573 | no | 0 | 0 | buy_bybit_sell_fluxion |
| NVDAx | yes | 0.3325 | no | 0 | 0 | buy_bybit_sell_fluxion |
| TSLAx | yes | 2.5014 | no | 0 | 0 | buy_bybit_sell_fluxion |

## Fitted `min_edge_bps` (AMM, for bot config)

Per (symbol × session) at the **$1,000** rung, best direction by zero-threshold profit/day. Knee fit = highest threshold retaining ≥70% of that direction's capturable profit (windows filtered from the T=0 set by ``peak_edge_bps``). Rows marked **ceiling** hit the top of the 200 bps extended sweep still above the capture bar — treat that as a lower bound, not a tight optimum (or state that the optimum is unreachable within 200 bps).

| Symbol | Session | Direction | Fit bps | Flag | Windows/day | Profit/day (USDT) | Fitted? | N samples |
|--------|---------|-----------|--------:|------|------------:|------------------:|---------|----------:|
| AAPLx | open | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 1542 |
| AAPLx | closed | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 4564 |
| CRCLx | open | buy_fluxion_sell_bybit | 2 | — | 1.50 | 0.4862 | True | 1655 |
| CRCLx | closed | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 3452 |
| GOOGLx | open | buy_bybit_sell_fluxion | 0 | — | 1.50 | 0.2864 | True | 1592 |
| GOOGLx | closed | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 3330 |
| HOODx | open | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 1672 |
| HOODx | closed | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 2504 |
| METAx | open | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 1598 |
| METAx | closed | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 3841 |
| NVDAx | open | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 1673 |
| NVDAx | closed | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 3838 |
| TSLAx | open | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 1675 |
| TSLAx | closed | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 4556 |

## Window statistics (selected thresholds)

Selected thresholds 0 / 10 / 20 / 40 / 60 / 100 bps are tabulated below; the companion JSON keeps selected rows per series (re-run the script for the full extended sweep in memory).

### AMM $1,000 — RTH open

| Symbol | Dir | T=0 w/d | T=0 $/d | T=20 w/d | T=20 $/d | T=40 w/d | T=40 $/d | T=60 w/d | T=60 $/d | T=100 w/d | T=100 $/d |
|--------|-----|--------:|--------:|---------:|---------:|---------:|---------:|---------:|---------:|----------:|----------:|
| AAPLx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| AAPLx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| CRCLx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| CRCLx | buy_fluxion_sell | 1.50 | 0.4862 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| GOOGLx | buy_bybit_sell_f | 1.50 | 0.2864 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| GOOGLx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| HOODx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| HOODx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| METAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| METAx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| NVDAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| NVDAx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| TSLAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| TSLAx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |

### AMM $1,000 — closed session

| Symbol | Dir | T=0 w/d | T=0 $/d | T=20 w/d | T=20 $/d | T=40 w/d | T=40 $/d | T=60 w/d | T=60 $/d |
|--------|-----|--------:|--------:|---------:|---------:|---------:|---------:|---------:|---------:|
| AAPLx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| AAPLx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| CRCLx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| CRCLx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| GOOGLx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| GOOGLx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| HOODx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| HOODx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| METAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| METAx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| NVDAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| NVDAx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| TSLAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| TSLAx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |

### AMM $500 — RTH open (summary profit/day at T=0)

| Symbol | Dir | Windows/day | Profit/day (USDT) | N samples |
|--------|-----|------------:|------------------:|----------:|
| AAPLx | buy_bybit_sell_f | 5.50 | 0.5446 | 1542 |
| AAPLx | buy_fluxion_sell | 0.00 | 0 | 1542 |
| CRCLx | buy_bybit_sell_f | 6.50 | 1.4081 | 1655 |
| CRCLx | buy_fluxion_sell | 3.00 | 7.9296 | 1655 |
| GOOGLx | buy_bybit_sell_f | 2.50 | 3.0632 | 1592 |
| GOOGLx | buy_fluxion_sell | 0.00 | 0 | 1592 |
| HOODx | buy_bybit_sell_f | 2.50 | 0.8638 | 1672 |
| HOODx | buy_fluxion_sell | 4.00 | 2.1196 | 1672 |
| METAx | buy_bybit_sell_f | 2.50 | 0.3573 | 1598 |
| METAx | buy_fluxion_sell | 0.00 | 0 | 1598 |
| NVDAx | buy_bybit_sell_f | 0.00 | 0 | 1673 |
| NVDAx | buy_fluxion_sell | 2.00 | 0.3325 | 1673 |
| TSLAx | buy_bybit_sell_f | 2.50 | 0.2136 | 1675 |
| TSLAx | buy_fluxion_sell | 8.00 | 2.5014 | 1675 |

### RFQ — RTH open (poll-native size, capped at $1,000)

| Symbol | Dir | T=0 w/d | T=0 $/d | T=10 w/d | T=10 $/d | Fit bps | Fitted? |
|--------|-----|--------:|--------:|---------:|---------:|--------:|---------|
| AAPLx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0 | False |
| AAPLx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0 | False |
| CRCLx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0 | False |
| GOOGLx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0 | False |
| GOOGLx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0 | False |
| HOODx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0 | False |
| METAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0 | False |
| METAx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0 | False |
| NVDAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0 | False |
| NVDAx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0 | False |
| SPCXx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0 | False |
| SPCXx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0 | False |
| TSLAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0 | False |
| TSLAx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0 | False |

## Closed-session note

AMM $1000 closed-session capturable profit (sum of per-series single-flight, **not** portfolio-deconflicted): 0.00 USDT over the span (0.00/day). Closed-session RFQ remains two-sided on liquid pairs (see `docs/references/m4-closed-session-rfq.md`); session ≠ mechanism.

## RFQ vs AMM (v2 signal)

RFQ open-session sum of per-series capturable profit at T=0: 0.00 USDT (0.00/day). Not portfolio-deconflicted against AMM; headline go/no-go ignores RFQ.

## Fit quality & next steps

- Primary verdict **NO-GO** under taker 20 / rebalance 1 / live basis (0.7726 USDT/day, 2 stable symbols).
- No fit pinned at the 200 bps sweep ceiling (extended grid bracketed the knee).
- WHI-866 comparison: prior GO at 66 USDT/day / 5 symbols used 10 bps taker + 0 basis + 0 rebalance and only 5.69 h clean RTH.
- Wire any surviving go-list thresholds into bot M4 (WHI-876) only after both §1.4 legs pass on a longer clean span.
- RFQ fill-firmness remains a separate gate before any RFQ leg (see WHI-908 on-chain fill validation).

---

*Companion machine-readable payload: `docs/references/m8-xstocks-edge-quant.json` (schema version 2).*
