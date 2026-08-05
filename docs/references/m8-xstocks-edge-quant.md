# xStocks arb edge quantification & threshold fit (WHI-866 / M8)

Go/no-go report for the sibling execution project `mantle-stocks-arbitrage-bots`. Headline numbers are **AMM-only** (bot v1); RFQ is reported separately as a v2 candidate.

**Generated:** 2026-08-05 13:48:36 UTC

## Regeneration

```bash
uv run python scripts/xstocks_edge_quant.py --db data/monitor-bybit-fluxion.db --sample-ms 15000
```

Pure helpers: `monitor.analysis.edge_quant` (unit-tested).
Venue math: `monitor.metrics.pnl_v2.compute_pnl_usd` (not reimplemented).

## Decision rule (bot DESIGN §1.4)

After all costs (Bybit taker 10 bps + Fluxion pool fee + bilateral slip + gas):

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
| sample_ms | 15000 |
| align_ms | 15000 |
| reentry_cooldown_ms | 86400000 |
| trade_duration_ms | 5000 |
| max_gap_ms | 120000 |
| threshold_sweep_bps | 0..60 step 2 |
| capture_fraction | 0.70 |
| inventory_usd | 5000 |
| max_trade_usd | 1000 |
| pricing_anomaly_gate | 500 |

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
| `bybit_book` | 976,690 | 2026-08-03 14:51:38 UTC | 2026-08-05 13:47:53 UTC | CEX L1 (primary timeline; ~2d raw retention) |
| `bybit_depth` | 679,760 | 2026-08-03 14:51:38 UTC | 2026-08-05 13:47:53 UTC | CEX VWAP curve for $500/$1k slip (~2d retention) |
| `fluxion_pool_state` | 635,768 | 2026-08-02 03:18:24 UTC | 2026-08-05 13:47:53 UTC | AMM geometry (as-of join; ~7d retention) |
| `fluxion_rfq_quotes` | 58,216 | 2026-08-02 13:19:15 UTC | 2026-08-05 13:47:53 UTC | RFQ polls (separate column; ~3d retention) |
| `bybit_book_1m` | 14,634 | 2026-08-02 03:18:00 UTC | 2026-08-03 12:10:00 UTC | Survives longer (~14d) but **not used** (no depth) |
| `collector_gaps` | 2,334 | 2026-08-02 03:18:32 UTC | 2026-08-05 13:47:03 UTC | Downtime exclusion (`source=collector_down`) |

### Per-symbol coverage (raw `bybit_book`)

Study window wall clock: **2026-08-03 14:51:38 UTC → 2026-08-05 13:47:53 UTC** (46.9 h). RTH hours in window: **11.94 h** (excl. collector_down: **5.29 h**). Closed hours: **35.00 h** (excl. gap: **21.75 h**).

**Collector downtime:** 19 `collector_down` intervals totaling **22.77 h** (of which **6.65 h** fell inside RTH). Samples inside those intervals are excluded.

| Pair | Book rows | First | Last | AMM pool? | Notes |
|------|----------:|-------|------|-----------|-------|
| AAPLx | 85,676 | 2026-08-03 14:51:38 UTC | 2026-08-05 13:47:53 UTC | yes | — |
| AMZNx | 55,356 | 2026-08-03 14:51:39 UTC | 2026-08-05 13:47:54 UTC | no | CEX-only (no Fluxion pool) |
| COINx | 64,310 | 2026-08-03 14:51:39 UTC | 2026-08-05 13:47:54 UTC | no | CEX-only (no Fluxion pool) |
| CRCLx | 77,998 | 2026-08-03 14:51:38 UTC | 2026-08-05 13:47:54 UTC | yes | — |
| GOOGLx | 59,343 | 2026-08-03 14:51:38 UTC | 2026-08-05 13:47:53 UTC | yes | — |
| HOODx | 38,753 | 2026-08-03 14:51:39 UTC | 2026-08-05 13:47:54 UTC | yes | — |
| MCDx | 72,351 | 2026-08-03 14:51:39 UTC | 2026-08-05 13:47:54 UTC | no | CEX-only (no Fluxion pool) |
| METAx | 54,361 | 2026-08-03 14:51:39 UTC | 2026-08-05 13:47:54 UTC | yes | — |
| NVDAx | 111,096 | 2026-08-03 14:51:39 UTC | 2026-08-05 13:47:54 UTC | yes | — |
| SPCXx | 165,041 | 2026-08-03 14:51:39 UTC | 2026-08-05 13:47:54 UTC | yes | frequent pricing_anomaly / thin pool — often excluded by gate |
| TSLAx | 192,454 | 2026-08-03 14:51:39 UTC | 2026-08-05 13:47:54 UTC | yes | — |

> **Coverage caveat:** effective RTH after downtime is under 6 hours. Fit quality is **low** — treat thresholds as provisional and re-run after ≥5 clean RTH sessions.

## Hygiene / exclusions

- No corporate-action calendar was applied: observation window is 2026-08-03 14:51:38 UTC → 2026-08-05 13:47:53 UTC and no xStock dividend/split/rebase was announced in-span for the tracked names (manual check; automate later per bot DESIGN §8).
- No bStocks-style share rebase applies to Fluxion xStocks wrapper mechanics in this window; no segment break was introduced.
- Samples with `gap=1` on book/pool/depth rows are dropped at load.
- Pairs failing `amm_quote_for_cex` (empty_pool / invalid_mid / pricing_anomaly, default |spread| > 500 bps) are excluded from AMM samples for that timestamp (SPCXx frequently hits this).
- CEX-only inventory pairs (no Fluxion AMM) are out of scope for the bot v1 AMM path and appear only in the coverage table.

## Headline go/no-go (AMM-only, portfolio single-flight)

| Metric | Value |
|--------|------:|
| Study calendar days (span/86400s) | 1.956 |
| Portfolio capturable profit (total, $1000 rung) | 122.1397 USDT |
| **Average capturable profit / day** | **62.4524 USDT/day** |
| Symbols with fitted stable windows (open, $1000) | 5 |
| Go-list | CRCLx, GOOGLx, HOODx, NVDAx, TSLAx |
| **Verdict** | **GO** |

Portfolio average **62.45 USDT/day** meets the ≥15 gate with **5** symbols showing positive fitted open-session windows (CRCLx, GOOGLx, HOODx, NVDAx, TSLAx).

## Fitted `min_edge_bps` (AMM, for bot config)

Per (symbol × session) at the **$1,000** rung, best direction by zero-threshold profit/day. Knee fit = highest threshold retaining ≥70% of that direction's capturable profit.

| Symbol | Session | Direction | Fit bps | Windows/day | Profit/day (USDT) | Fitted? | N samples |
|--------|---------|-----------|--------:|------------:|------------------:|---------|----------:|
| AAPLx | open | buy_bybit_sell_fluxion | 0 | 0.00 | 0 | False | 1191 |
| AAPLx | closed | buy_bybit_sell_fluxion | 0 | 0.00 | 0 | False | 4188 |
| CRCLx | open | buy_fluxion_sell_bybit | 60 | 1.02 | 9.3161 | True | 1235 |
| CRCLx | closed | buy_fluxion_sell_bybit | 60 | 1.53 | 13.8804 | True | 3830 |
| GOOGLx | open | buy_fluxion_sell_bybit | 12 | 0.51 | 0.6472 | True | 1241 |
| GOOGLx | closed | buy_fluxion_sell_bybit | 32 | 1.02 | 4.1562 | True | 3513 |
| HOODx | open | buy_fluxion_sell_bybit | 60 | 2.56 | 40.2510 | True | 1183 |
| HOODx | closed | buy_fluxion_sell_bybit | 54 | 0.51 | 2.8153 | True | 3274 |
| METAx | open | buy_bybit_sell_fluxion | 0 | 0.00 | 0 | False | 1219 |
| METAx | closed | buy_bybit_sell_fluxion | 0 | 0.00 | 0 | False | 3128 |
| NVDAx | open | buy_fluxion_sell_bybit | 60 | 2.04 | 13.8624 | True | 1242 |
| NVDAx | closed | buy_fluxion_sell_bybit | 28 | 0.51 | 1.4744 | True | 4439 |
| TSLAx | open | buy_fluxion_sell_bybit | 28 | 0.51 | 1.4545 | True | 1243 |
| TSLAx | closed | buy_bybit_sell_fluxion | 0 | 0.00 | 0 | False | 4691 |

## Window statistics (selected thresholds)

Full 0..60 / step-2 sweeps are in the companion JSON. Tables below show thresholds 0 / 10 / 20 / 40 bps for AMM $1,000 (open session) and RFQ (all sizes pooled as poll-native, open session).

### AMM $1,000 — RTH open

| Symbol | Dir | T=0 w/d | T=0 $/d | T=10 w/d | T=10 $/d | T=20 w/d | T=20 $/d | T=40 w/d | T=40 $/d |
|--------|-----|--------:|--------:|---------:|---------:|---------:|---------:|---------:|---------:|
| AAPLx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| AAPLx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| CRCLx | buy_bybit_sell_f | 1.02 | 1.4648 | 1.02 | 1.4648 | 0.51 | 1.1873 | 0.00 | 0 |
| CRCLx | buy_fluxion_sell | 5.11 | 11.5013 | 4.60 | 15.5509 | 2.56 | 13.2224 | 1.02 | 9.3161 |
| GOOGLx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| GOOGLx | buy_fluxion_sell | 1.02 | 0.8349 | 0.51 | 0.6472 | 0.00 | 0 | 0.00 | 0 |
| HOODx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| HOODx | buy_fluxion_sell | 5.11 | 34.4204 | 6.14 | 39.2884 | 5.62 | 43.6510 | 4.60 | 47.8528 |
| METAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| METAx | buy_fluxion_sell | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| NVDAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| NVDAx | buy_fluxion_sell | 1.02 | 2.2165 | 1.02 | 2.2165 | 1.53 | 3.8347 | 2.56 | 11.0878 |
| TSLAx | buy_bybit_sell_f | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 | 0.00 | 0 |
| TSLAx | buy_fluxion_sell | 0.51 | 0.0999 | 0.51 | 0.6277 | 1.53 | 3.6083 | 0.00 | 0 |

### AMM $500 — RTH open (summary profit/day at T=0)

| Symbol | Dir | Windows/day | Profit/day (USDT) | N samples |
|--------|-----|------------:|------------------:|----------:|
| AAPLx | buy_bybit_sell_f | 4.09 | 0.8445 | 1191 |
| AAPLx | buy_fluxion_sell | 0.00 | 0 | 1191 |
| CRCLx | buy_bybit_sell_f | 3.07 | 8.5853 | 1235 |
| CRCLx | buy_fluxion_sell | 5.62 | 11.4991 | 1235 |
| GOOGLx | buy_bybit_sell_f | 0.00 | 0 | 1241 |
| GOOGLx | buy_fluxion_sell | 10.23 | 5.4484 | 1241 |
| HOODx | buy_bybit_sell_f | 0.00 | 0 | 1183 |
| HOODx | buy_fluxion_sell | 14.83 | 25.2691 | 1183 |
| METAx | buy_bybit_sell_f | 0.00 | 0 | 1219 |
| METAx | buy_fluxion_sell | 6.14 | 0.9615 | 1219 |
| NVDAx | buy_bybit_sell_f | 0.00 | 0 | 1242 |
| NVDAx | buy_fluxion_sell | 15.34 | 5.7115 | 1242 |
| TSLAx | buy_bybit_sell_f | 0.00 | 0 | 1243 |
| TSLAx | buy_fluxion_sell | 2.04 | 2.3432 | 1243 |

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

AMM $1000 closed-session capturable profit (sum of per-series single-flight, **not** portfolio-deconflicted): 35.52 USDT over the span (18.16/day). Closed-session RFQ remains two-sided on liquid pairs (see `docs/references/m4-closed-session-rfq.md`); session ≠ mechanism.

## RFQ vs AMM (v2 signal)

RFQ open-session sum of per-series capturable profit at T=0: 0.00 USDT (0.00/day). This is **not** portfolio-deconflicted against AMM and uses poll quotes that are vendor-asserted but not fill-verified — RFQ stays v2 until a fill-firmness test lands. Headline go/no-go ignores RFQ.

## Fit quality & next steps

- Re-run after ≥5 consecutive clean RTH sessions with `collector_down` RTH loss < 1 h/day — current fit quality is limited by downtime inside the 2-day raw retention window.
- Wire the fit table into the bot repo M4 threshold config (WHI-876); start with CRCLx / HOODx / NVDAx (material open-session profit/day) and treat GOOGLx / TSLAx as optional add-ons.
- Manually review large HOODx dislocations (gross basis near the 500 bps pricing_anomaly gate) on a fill before sizing up — paper fillable ≠ firm when AMM mid is slow to update.
- RFQ fill-firmness remains a separate gate before any RFQ leg.

---

*Companion machine-readable payload: `docs/references/m8-xstocks-edge-quant.json` (schema version 1).*
