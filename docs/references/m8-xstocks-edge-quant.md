# xStocks arb edge quantification & threshold fit (WHI-866 / M8)

Go/no-go report for the sibling execution project `mantle-stocks-arbitrage-bots`. Headline numbers are **AMM-only** (bot v1); RFQ is reported separately as a v2 candidate.

**Generated:** 2026-08-05 14:49:46 UTC

> **Stale cost basis (WHI-959 + WHI-960):** this report was generated under the
> old 10 bps Bybit taker and **0** USDT/USDC basis. Live config is **20 bps**
> Adventure Zone taker and **signed 7.5 bps** USDC premium (dir1 charged, dir2
> credited). Headline is dir1-only (`buy_fluxion_sell_bybit`), so capturable
> profit overstates net edge by ~10 bps taker + ~7.5 bps basis per trade until
> re-run under WHI-909.

## Regeneration

```bash
uv run python scripts/xstocks_edge_quant.py --db data/monitor-bybit-fluxion.db --sample-ms 20000
```

Pure helpers: `monitor.analysis.edge_quant` (unit-tested).
Venue math: `monitor.metrics.pnl_v2.compute_pnl_usd` (not reimplemented).

## Decision rule (bot DESIGN §1.4)

After all costs (Bybit taker 10 bps + Fluxion pool fee + bilateral slip + gas)
*(historical run cost stack — see staleness banner above)*:

- **Go:** average capturable profit ≥ **15 USDT/day** at 5000 USDT inventory with ≤ **1000 USDT** per trade, **and** ≥ **3 symbols** with stable windows.
- **No-go:** < **5 USDT/day**.
- In between: judge on window time-distribution (open-auction-only clusters discount heavily).

## Method

| Item | Value |
|------|--------|
| market | bybit-fluxion |
| engine | monitor.metrics.pnl_v2.compute_pnl_usd |
| analysis | monitor.analysis.edge_quant |
| notionals_usd | 500, 1000 |
| sample_ms | 20000 |
| align_ms | 15000 |
| reentry_cooldown_ms | 86400000 |
| trade_duration_ms | 5000 |
| max_gap_ms | 120000 |
| threshold_sweep_bps | 0..60 step 2 |
| capture_fraction | 0.70 |
| inventory_usd | 5000 |
| max_trade_usd | 1000 |
| pricing_anomaly_gate | 500 (study-time default; **WHI-964** shipped config is **300**) |

### Capturable-profit model

- **Single-flight:** at most one trade in flight (series-level for per-symbol tables; portfolio-level for the go/no-go headline).
- **Per-trade cap:** ≤ 1000 USDT (bot max). Analysis rungs: $500 and $1,000.
- **Re-entry cooldown:** 86400000 ms after trade_duration=5000 ms. Default is one calendar day so the headline is **one trade per window** (fire-on-open PnL). Pass a shorter `--reentry-cooldown-ms` for a multi-entry sensitivity.
- **Threshold fit:** highest `min_edge_bps` on the 0..60 / step-2 sweep that still retains ≥ 70% of zero-threshold capturable profit (knee).
- **AMM vs RFQ:** separate columns; RFQ sizes are poll-native (scaled down only when poll notional > $1,000). No AMM impact curve is interpolated onto RFQ rows.

## Data span inventory

### Journal tables used

Retention (`config/collector.yaml` → `retention`) prunes raw Bybit L1 at ~2 days and depth at ~2 days; pool state ~7 days; RFQ polls ~3 days; `bybit_book_1m` downsample ~14 days. **This study uses raw aligned ticks only** (depth-aware PnL v2) — not the 1m bars — because capturable profit at $500/$1,000 requires the depth curve and pool geometry, not L1 alone.

| Table | Rows | Min (UTC) | Max (UTC) | Role |
|-------|-----:|-----------|-----------|------|
| `bybit_book` | 1,026,638 | 2026-08-03 14:51:38 UTC | 2026-08-05 14:48:15 UTC | CEX L1 (primary timeline; ~2d raw retention) |
| `bybit_depth` | 707,658 | 2026-08-03 14:51:38 UTC | 2026-08-05 14:48:16 UTC | CEX VWAP curve for $500/$1k slip (~2d retention) |
| `fluxion_pool_state` | 640,128 | 2026-08-02 03:18:24 UTC | 2026-08-05 14:48:22 UTC | AMM geometry (as-of join; ~7d retention) |
| `fluxion_rfq_quotes` | 56,012 | 2026-08-02 14:39:25 UTC | 2026-08-05 14:48:20 UTC | RFQ polls (separate column; ~3d retention) |
| `bybit_book_1m` | 14,634 | 2026-08-02 03:18:00 UTC | 2026-08-03 12:10:00 UTC | Survives longer (~14d) but **not used** (no depth) |
| `collector_gaps` | 2,372 | 2026-08-02 03:18:32 UTC | 2026-08-05 14:48:15 UTC | Downtime exclusion (`source=collector_down`) |

### Per-symbol coverage (raw `bybit_book`)

Study window wall clock: **2026-08-03 14:51:38 UTC → 2026-08-05 14:48:15 UTC** (47.9 h). RTH hours in window: **12.94 h** (excl. collector_down: **5.69 h**). Closed hours: **35.00 h** (excl. gap: **21.75 h**).

**Collector downtime:** 21 `collector_down` intervals totaling **23.37 h** (of which **7.25 h** fell inside RTH). Samples inside those intervals are excluded.

| Pair | Book rows | First | Last | AMM pool? | Notes |
|------|----------:|-------|------|-----------|-------|
| AAPLx | 87,129 | 2026-08-03 14:51:38 UTC | 2026-08-05 14:48:23 UTC | yes | — |
| AMZNx | 57,217 | 2026-08-03 14:51:39 UTC | 2026-08-05 14:48:17 UTC | no | CEX-only (no Fluxion pool) |
| COINx | 69,546 | 2026-08-03 14:51:39 UTC | 2026-08-05 14:48:23 UTC | no | CEX-only (no Fluxion pool) |
| CRCLx | 83,402 | 2026-08-03 14:51:38 UTC | 2026-08-05 14:48:23 UTC | yes | — |
| GOOGLx | 61,133 | 2026-08-03 14:51:38 UTC | 2026-08-05 14:48:23 UTC | yes | — |
| HOODx | 44,435 | 2026-08-03 14:51:39 UTC | 2026-08-05 14:48:22 UTC | yes | — |
| MCDx | 73,281 | 2026-08-03 14:51:39 UTC | 2026-08-05 14:48:22 UTC | no | CEX-only (no Fluxion pool) |
| METAx | 57,023 | 2026-08-03 14:51:39 UTC | 2026-08-05 14:48:19 UTC | yes | — |
| NVDAx | 119,566 | 2026-08-03 14:51:39 UTC | 2026-08-05 14:48:23 UTC | yes | — |
| SPCXx | 175,016 | 2026-08-03 14:51:39 UTC | 2026-08-05 14:48:23 UTC | yes | frequent pricing_anomaly / thin pool — often excluded by gate |
| TSLAx | 199,090 | 2026-08-03 14:51:39 UTC | 2026-08-05 14:48:23 UTC | yes | — |

> **Coverage caveat:** effective RTH after downtime is under 6 hours. Fit quality is **low** — treat thresholds as provisional and re-run after ≥5 clean RTH sessions.

## Hygiene / exclusions

- No automated corporate-action calendar is applied. The observation window is 2026-08-03 14:51:38 UTC → 2026-08-05 14:48:15 UTC; re-runs over a longer span must re-check dividends/splits/rebases and disclose any excluded days (bot DESIGN §8).
- No bStocks-style share rebase segment break was introduced for Fluxion xStocks wrappers in this study.
- Samples with `gap=1` on book/pool/depth rows are dropped at load.
- Pairs failing `amm_quote_for_cex` (empty_pool / invalid_mid / pricing_anomaly, study gate |spread| > 500 bps; **WHI-964** config default is 300) are excluded from AMM samples for that timestamp (SPCXx frequently hits this).
- CEX-only inventory pairs (no Fluxion AMM) are out of scope for the bot v1 AMM path and appear only in the coverage table.
- Headline portfolio mixes open+closed AMM $1000 windows at T=0 (one trade per window, single-flight). Go-list symbols are open-session fits only.

## Headline go/no-go (AMM-only, portfolio single-flight)

| Metric | Value |
|--------|------:|
| Study calendar days (span/86400s) | 1.998 |
| Portfolio capturable profit (total, $1000 rung) | 132.4571 USDT |
| **Average capturable profit / day** | **66.3065 USDT/day** |
| Symbols with fitted stable windows (open, $1000) | 5 |
| Go-list | CRCLx, GOOGLx, HOODx, NVDAx, TSLAx |
| Core go-list (≥1 USDT/day open fit) | CRCLx, HOODx, NVDAx |
| Portfolio $/day excluding HOODx | 32.9950 |
| **Verdict** | **GO** |

Portfolio average **66.31 USDT/day** meets the ≥15 gate with **5** symbols showing positive fitted open-session windows (CRCLx, GOOGLx, HOODx, NVDAx, TSLAx). Core (≥1 USDT/day): CRCLx, HOODx, NVDAx.

**Concentration:** HOODx open fit contributes 33.6705 USDT/day of series-level profit; portfolio single-flight excluding all HOODx windows is **33.00 USDT/day** (still ≥15). Core go-list (≥1 USDT/day open fit): CRCLx, HOODx, NVDAx. GOOGLx/TSLAx seats are thin — provisional only.

## Fitted `min_edge_bps` (AMM, for bot config)

Per (symbol × session) at the **$1,000** rung, best direction by zero-threshold profit/day. Knee fit = highest threshold retaining ≥70% of that direction's capturable profit (windows filtered from the T=0 set by ``peak_edge_bps``). Rows marked **ceiling** hit the top of the 0..60 sweep still above the capture bar — treat 60 as a lower bound, not a tight optimum.

| Symbol | Session | Direction | Fit bps | Flag | Windows/day | Profit/day (USDT) | Fitted? | N samples |
|--------|---------|-----------|--------:|------|------------:|------------------:|---------|----------:|
| AAPLx | open | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 984 |
| AAPLx | closed | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 3251 |
| CRCLx | open | buy_fluxion_sell_bybit | 60 | ceiling | 1.00 | 9.8162 | True | 998 |
| CRCLx | closed | buy_fluxion_sell_bybit | 52 | — | 1.00 | 6.6888 | True | 3122 |
| GOOGLx | open | buy_fluxion_sell_bybit | 12 | — | 0.50 | 0.6336 | True | 1000 |
| GOOGLx | closed | buy_fluxion_sell_bybit | 30 | — | 1.00 | 4.0303 | True | 2877 |
| HOODx | open | buy_fluxion_sell_bybit | 60 | ceiling | 2.00 | 33.6705 | True | 969 |
| HOODx | closed | buy_fluxion_sell_bybit | 16 | — | 2.00 | 4.8246 | True | 2754 |
| METAx | open | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 991 |
| METAx | closed | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 2564 |
| NVDAx | open | buy_fluxion_sell_bybit | 60 | ceiling | 1.00 | 2.1093 | True | 1001 |
| NVDAx | closed | buy_fluxion_sell_bybit | 4 | — | 1.50 | 0.2993 | True | 3481 |
| TSLAx | open | buy_fluxion_sell_bybit | 30 | — | 0.50 | 0.0610 | True | 1001 |
| TSLAx | closed | buy_bybit_sell_fluxion | 0 | — | 0.00 | 0 | False | 3600 |

## Window statistics (selected thresholds)

Selected thresholds 0 / 10 / 20 / 40 bps are tabulated below; the companion JSON keeps the same selected rows per series (re-run the script to rebuild the full 0..60 / step-2 sweep in memory).

### AMM $1,000 — RTH open

| Symbol | Dir | T=0 w/d | T=0 $/d | T=10 w/d | T=10 $/d | T=20 w/d | T=20 $/d | T=40 w/d | T=40 $/d |
|--------|-----|--------:|--------:|---------:|---------:|---------:|---------:|---------:|---------:|
| AAPLx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| AAPLx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| CRCLx | buy_bybit_sell_f | 1.50 | 2.2344 | 1.00 | 2.1185 | 0.50 | 1.4192 | 0.00 | 0 |
| CRCLx | buy_fluxion_sell | 4.00 | 13.4847 | 2.00 | 12.6375 | 2.00 | 12.6375 | 1.00 | 9.8162 |
| GOOGLx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| GOOGLx | buy_fluxion_sell | 1.00 | 0.8995 | 0.50 | 0.6336 | 0.00 | 0 | 0.00 | 0 |
| HOODx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| HOODx | buy_fluxion_sell | 5.01 | 34.3003 | 4.00 | 34.2132 | 2.50 | 33.7767 | 2.00 | 33.6705 |
| METAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| METAx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| NVDAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| NVDAx | buy_fluxion_sell | 1.00 | 2.1093 | 1.00 | 2.1093 | 1.00 | 2.1093 | 1.00 | 2.1093 |
| TSLAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| TSLAx | buy_fluxion_sell | 0.50 | 0.0610 | 0.50 | 0.0610 | 0.50 | 0.0610 | 0.00 | 0 |

### AMM $1,000 — closed session

| Symbol | Dir | T=0 w/d | T=0 $/d | T=10 w/d | T=10 $/d | T=20 w/d | T=20 $/d | T=40 w/d | T=40 $/d |
|--------|-----|--------:|--------:|---------:|---------:|---------:|---------:|---------:|---------:|
| AAPLx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| AAPLx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| CRCLx | buy_bybit_sell_f | 1.00 | 3.2088 | 1.00 | 3.2088 | 1.00 | 3.2088 | 1.00 | 3.2088 |
| CRCLx | buy_fluxion_sell | 1.00 | 6.6888 | 1.00 | 6.6888 | 1.00 | 6.6888 | 1.00 | 6.6888 |
| GOOGLx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| GOOGLx | buy_fluxion_sell | 1.50 | 4.5939 | 1.50 | 4.5939 | 1.00 | 4.0303 | 0.50 | 2.4539 |
| HOODx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| HOODx | buy_fluxion_sell | 5.01 | 6.0692 | 3.50 | 5.8357 | 1.50 | 3.9280 | 0.50 | 2.7561 |
| METAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| METAx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| NVDAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| NVDAx | buy_fluxion_sell | 2.00 | 0.3482 | 1.00 | 0.2269 | 1.00 | 0.2269 | 0.00 | 0 |
| TSLAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| TSLAx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |

### AMM $500 — RTH open (summary profit/day at T=0)

| Symbol | Dir | Windows/day | Profit/day (USDT) | N samples |
|--------|-----|------------:|------------------:|----------:|
| AAPLx | buy_bybit_sell_f | 3.50 | 0.7809 | 984 |
| AAPLx | buy_fluxion_sell | 0.00 | 0 | 984 |
| CRCLx | buy_bybit_sell_f | 3.50 | 9.6512 | 998 |
| CRCLx | buy_fluxion_sell | 6.01 | 11.7084 | 998 |
| GOOGLx | buy_bybit_sell_f | 0.00 | 0 | 1000 |
| GOOGLx | buy_fluxion_sell | 8.51 | 4.9850 | 1000 |
| HOODx | buy_bybit_sell_f | 0.00 | 0 | 969 |
| HOODx | buy_fluxion_sell | 13.02 | 25.0719 | 969 |
| METAx | buy_bybit_sell_f | 0.00 | 0 | 991 |
| METAx | buy_fluxion_sell | 5.51 | 1.1150 | 991 |
| NVDAx | buy_bybit_sell_f | 0.00 | 0 | 1001 |
| NVDAx | buy_fluxion_sell | 15.02 | 6.7281 | 1001 |
| TSLAx | buy_bybit_sell_f | 0.00 | 0 | 1001 |
| TSLAx | buy_fluxion_sell | 2.50 | 2.1802 | 1001 |

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

AMM $1000 closed-session capturable profit (sum of per-series single-flight, **not** portfolio-deconflicted): 41.77 USDT over the span (20.91/day). Closed-session RFQ remains two-sided on liquid pairs (see `docs/references/m4-closed-session-rfq.md`); session ≠ mechanism.

## RFQ vs AMM (v2 signal)

RFQ open-session sum of per-series capturable profit at T=0: 0.00 USDT (0.00/day). This is **not** portfolio-deconflicted against AMM and uses poll quotes that are vendor-asserted but not fill-verified — RFQ stays v2 until a fill-firmness test lands. Headline go/no-go ignores RFQ.

## Fit quality & next steps

- Re-run after ≥5 consecutive clean RTH sessions with `collector_down` RTH loss < 1 h/day — current fit quality is limited by downtime inside the 2-day raw retention window.
- Wire the fit table into the bot repo M4 threshold config (WHI-876); start with CRCLx / HOODx / NVDAx (material open-session profit/day) and treat GOOGLx / TSLAx as optional add-ons.
- Manually review large HOODx dislocations (gross basis near the study-time 500 bps pricing_anomaly gate; shipped gate is 300 since WHI-964) on a fill before sizing up — paper fillable ≠ firm when AMM mid is slow to update.
- RFQ fill-firmness remains a separate gate before any RFQ leg.

---

*Companion machine-readable payload: `docs/references/m8-xstocks-edge-quant.json` (schema version 1).*
