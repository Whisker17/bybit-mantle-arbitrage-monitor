# On-chain fill validation of M8 dislocation windows (WHI-908)

Did anyone actually swap during the large paper dislocation windows that drive the M8 go-case — or are those windows measurement artifacts?

**Generated:** 2026-08-07 03:09:35 UTC

## Regeneration

```bash
uv run python scripts/xstocks_fill_validation.py --db data/monitor-bybit-fluxion.db --sample-ms 20000
```

Pure helpers: `monitor.analysis.fill_validation` (unit-tested). Windows / PnL: same path as M0 (`monitor.analysis.edge_quant` + `monitor.metrics.pnl_v2.compute_pnl_usd`).

## Method

| Item | Value |
|------|--------|
| market | bybit-fluxion |
| engine | monitor.metrics.pnl_v2.compute_pnl_usd |
| windows | monitor.analysis.edge_quant.detect_windows |
| classification | monitor.analysis.fill_validation |
| size_usd | 1000 |
| sample_ms | 20000 |
| align_ms | 15000 |
| max_gap_ms | 120000 |
| trade_duration_ms | 5000 |
| reentry_cooldown_ms | 86400000 |
| min_edge_bps | 0 |
| pricing_anomaly_gate_default | 500 |
| top_n | 20 |
| inventory_usd | 5000 |
| max_trade_usd | 1000 |

### Classification

- **taken** — ≥1 on-chain `fluxion_swaps` row in the profitable AMM direction inside the window (`buy_fluxion_sell_bybit` → `buy_native`, `buy_bybit_sell_fluxion` → `sell_native`).
- **untaken_with_liquidity** — no matching swap, but UniV3 single-range math fills the study notional ($1,000) at window-open pool state.
- **untaken_too_thin** — no matching swap and the range cannot support $1,000 (paper opportunity never capturable at size).

### Data span note (retention)

M0 (`m8-xstocks-edge-quant.md`) used raw ticks spanning **2026-08-03 14:51 UTC → 2026-08-05 14:48 UTC**. Collector retention keeps raw `bybit_book` / `bybit_depth` for ~2 days, so that study window is **no longer fully present** in the journal. This note recomputes windows on the **currently retained** raw span (2026-08-05 02:12:00 UTC → 2026-08-07 03:08:37 UTC) with the same methodology (sample_ms=20000, align_ms=15000, size=$1000, T=0, pricing_anomaly gate=500 bps). `fluxion_pool_state` and `fluxion_swaps` still cover a longer history; swaps are joined on `recv_ts_ms` inside each detected window. Numbers are therefore a **method-matched re-run**, not a byte-for-byte replay of the M0 window list — the scientific question (are large paper dislocations taken on-chain?) is unchanged.

## Study span

- Wall clock: **2026-08-05 02:12:00 UTC → 2026-08-07 03:08:37 UTC** (48.94 h)
- Calendar days: **2.039**
- Journal swaps in span: **17** (across 8 pairs)

## Headline (portfolio single-flight, AMM $1,000, T=0)

| Metric | Value |
|--------|------:|
| Portfolio capturable profit (total) | 49.0568 USDT |
| Average capturable profit / day | 24.0554 USDT/day |
| N windows (all symbols) | 39 |
| Top-20 raw trade-PnL sum / portfolio | 101.0%¹ |
| Top-20 profit that is **taken** | 2.2494 USDT |
| Share of portfolio profit validated by real fills | **4.6%** |
| Share of top-20 profit validated by real fills | **4.5%** |

¹ Top-N sums each window's fire-on-open `trade_pnl_usd` without portfolio single-flight, so the ratio can exceed 100% when high-PnL windows overlap in time. Portfolio profit (row 1) is the single-flight figure.

### Classification counts (top-N)

| Class | Windows | Profit (USDT) | Share of top-N profit |
|-------|--------:|--------------:|----------------------:|
| taken | 2 | 2.2494 | 4.5% |
| untaken_with_liquidity | 18 | 47.3127 | 95.5% |
| untaken_too_thin | 0 | 0.0000 | 0.0% |

## Top windows

| # | Pair | Dir | Session | Start (UTC) | End (UTC) | Dur (s) | Peak edge (bps) | Trade PnL | Class | Swaps (match/tot) | Virtual quote $ | Pool age med (ms) | Max |basis| (bps) |
|--:|------|-----|---------|-------------|-----------|--------:|----------------:|----------:|-------|-----------------:|----------------:|------------------:|------------------:|
| 1 | CRCLx | buy_fluxion_sell | closed | 2026-08-05 10:17:19 UTC | 2026-08-05 10:18:59 UTC | 100.1 | 93.92 | 8.4845 | **untaken_with_liquidity** | 0/0 | 40602.8892 | 1244 | 383.5 |
| 2 | HOODx | buy_fluxion_sell | closed | 2026-08-05 08:06:17 UTC | 2026-08-05 08:06:48 UTC | 30.9 | 55.06 | 5.5057 | **untaken_with_liquidity** | 0/0 | 57522.0196 | 1846 | 267.2 |
| 3 | CRCLx | buy_bybit_sell | closed | 2026-08-05 11:19:19 UTC | 2026-08-05 11:23:19 UTC | 240.5 | 90.21 | 5.3885 | **untaken_with_liquidity** | 0/0 | 42068.4048 | 3122 | 392.4 |
| 4 | GOOGLx | buy_fluxion_sell | closed | 2026-08-05 08:03:19 UTC | 2026-08-05 08:03:19 UTC | 0.0 | 49.02 | 4.9020 | **untaken_with_liquidity** | 0/0 | 57739.5551 | 1637 | 264.1 |
| 5 | CRCLx | buy_fluxion_sell | closed | 2026-08-05 10:21:39 UTC | 2026-08-05 10:23:59 UTC | 140.1 | 52.79 | 4.8774 | **untaken_with_liquidity** | 0/0 | 41101.3892 | 1806 | 341.0 |
| 6 | GOOGLx | buy_fluxion_sell | closed | 2026-08-05 08:02:19 UTC | 2026-08-05 08:02:19 UTC | 0.0 | 31.49 | 3.1491 | **untaken_with_liquidity** | 0/0 | 57739.5551 | 3382 | 246.1 |
| 7 | CRCLx | buy_bybit_sell | open | 2026-08-05 13:32:19 UTC | 2026-08-05 13:32:19 UTC | 0.0 | 28.35 | 2.8350 | **untaken_with_liquidity** | 0/0 | 40043.8500 | 2698 | 353.7 |
| 8 | HOODx | buy_fluxion_sell | closed | 2026-08-05 08:02:59 UTC | 2026-08-05 08:03:57 UTC | 57.8 | 39.16 | 2.2216 | **untaken_with_liquidity** | 0/0 | 57522.0196 | 2760 | 248.8 |
| 9 | HOODx | buy_fluxion_sell | closed | 2026-08-05 08:02:18 UTC | 2026-08-05 08:02:18 UTC | 0.0 | 17.91 | 1.7910 | **untaken_with_liquidity** | 0/0 | 57522.0196 | 3179 | 227.3 |
| 10 | CRCLx | buy_fluxion_sell | open | 2026-08-06 15:09:39 UTC | 2026-08-06 15:10:19 UTC | 40.4 | 19.69 | 1.7621 | **untaken_with_liquidity** | 0/0 | 40569.2972 | 434 | 303.3 |
| 11 | GOOGLx | buy_bybit_sell | open | 2026-08-05 16:05:19 UTC | 2026-08-05 16:14:39 UTC | 559.7 | 229.32 | 1.6631 | **taken** | 1/1 | 57544.9447 | 1051 | 457.2 |
| 12 | GOOGLx | buy_fluxion_sell | closed | 2026-08-05 08:01:26 UTC | 2026-08-05 08:01:26 UTC | 0.0 | 11.26 | 1.1258 | **untaken_with_liquidity** | 0/0 | 57739.5551 | 3384 | 224.4 |
| 13 | HOODx | buy_fluxion_sell | closed | 2026-08-05 06:10:18 UTC | 2026-08-05 06:14:55 UTC | 277.2 | 14.05 | 1.0278 | **untaken_with_liquidity** | 0/0 | 57224.6750 | 2051 | 225.6 |
| 14 | CRCLx | buy_bybit_sell | closed | 2026-08-05 11:37:19 UTC | 2026-08-05 11:40:41 UTC | 201.2 | 98.35 | 1.0215 | **untaken_with_liquidity** | 0/0 | 41558.6230 | 3249 | 406.8 |
| 15 | METAx | buy_bybit_sell | open | 2026-08-05 16:11:39 UTC | 2026-08-05 16:12:58 UTC | 79.0 | 21.09 | 0.8760 | **untaken_with_liquidity** | 0/0 | 52042.9052 | 1738 | 254.6 |
| 16 | HOODx | buy_fluxion_sell | closed | 2026-08-05 05:54:49 UTC | 2026-08-05 06:07:18 UTC | 749.4 | 10.90 | 0.7647 | **untaken_with_liquidity** | 0/0 | 57224.6750 | 1225 | 223.0 |
| 17 | CRCLx | buy_fluxion_sell | open | 2026-08-06 15:08:39 UTC | 2026-08-06 15:08:59 UTC | 19.6 | 8.75 | 0.6698 | **untaken_with_liquidity** | 0/0 | 40569.2972 | 875 | 294.4 |
| 18 | GOOGLx | buy_bybit_sell | open | 2026-08-05 16:00:59 UTC | 2026-08-05 16:04:39 UTC | 220.1 | 235.70 | 0.6089 | **untaken_with_liquidity** | 0/0 | 58057.8985 | 1717 | 465.1 |
| 19 | NVDAx | buy_fluxion_sell | open | 2026-08-05 13:38:19 UTC | 2026-08-05 13:41:59 UTC | 220.0 | 64.48 | 0.5863 | **taken** | 1/1 | 55558.9373 | 1131 | 287.2 |
| 20 | GOOGLx | buy_bybit_sell | open | 2026-08-06 15:10:58 UTC | 2026-08-06 15:10:58 UTC | 0.0 | 3.01 | 0.3011 | **untaken_with_liquidity** | 0/0 | 57036.9532 | 892 | 220.7 |

### Taken windows — on-chain evidence

#### GOOGLx buy_bybit_sell_fluxion @ 2026-08-05 16:05:19 UTC (PnL 1.6631 USDT)

Profitable swap direction: `sell_native`. 1 matching / 1 total.

| recv (UTC) | block | tx | direction | notional $ | price |
|------------|------:|----|-----------|-----------:|------:|
| 2026-08-05 16:14:22 UTC | 98908057 | `0xe6fd1188…` | sell_native | 507.9916 | 367.6472 |

#### NVDAx buy_fluxion_sell_bybit @ 2026-08-05 13:38:19 UTC (PnL 0.5863 USDT)

Profitable swap direction: `buy_native`. 1 matching / 1 total.

| recv (UTC) | block | tx | direction | notional $ | price |
|------------|------:|----|-----------|-----------:|------:|
| 2026-08-05 13:41:36 UTC | 98903472 | `0xdd62e9e5…` | buy_native | 167.5710 | 216.7136 |

### Untaken windows — as-of join staleness

Pool age = book sample time − as-of `fluxion_pool_state.recv_ts_ms` (align gate = 15000 ms). High ages near the gate mean the window can be an as-of join artifact rather than a live dislocation.

| Pair | Class | Start | Trade PnL | Age min | Age med | Age p90 | Age max | N samples | Max |basis| (bps) | Depth OK |
|------|-------|-------|----------:|--------:|--------:|--------:|--------:|----------:|------------------:|---------:|
| CRCLx | untaken_with_liquidity | 2026-08-05 10:17:19 UTC | 8.4845 | 195 | 1244 | 6535 | 6535 | 6 | 383.5 | True |
| HOODx | untaken_with_liquidity | 2026-08-05 08:06:17 UTC | 5.5057 | 1081 | 1846 | 2081 | 2081 | 3 | 267.2 | True |
| CRCLx | untaken_with_liquidity | 2026-08-05 11:19:19 UTC | 5.3885 | 310 | 3122 | 4200 | 4274 | 13 | 392.4 | True |
| GOOGLx | untaken_with_liquidity | 2026-08-05 08:03:19 UTC | 4.9020 | 1637 | 1637 | 1637 | 1637 | 1 | 264.1 | True |
| CRCLx | untaken_with_liquidity | 2026-08-05 10:21:39 UTC | 4.8774 | 119 | 1806 | 8187 | 8187 | 8 | 341.0 | True |
| GOOGLx | untaken_with_liquidity | 2026-08-05 08:02:19 UTC | 3.1491 | 3382 | 3382 | 3382 | 3382 | 1 | 246.1 | True |
| CRCLx | untaken_with_liquidity | 2026-08-05 13:32:19 UTC | 2.8350 | 2698 | 2698 | 2698 | 2698 | 1 | 353.7 | True |
| HOODx | untaken_with_liquidity | 2026-08-05 08:02:59 UTC | 2.2216 | 1106 | 2760 | 5975 | 5975 | 4 | 248.8 | True |
| HOODx | untaken_with_liquidity | 2026-08-05 08:02:18 UTC | 1.7910 | 3179 | 3179 | 3179 | 3179 | 1 | 227.3 | True |
| CRCLx | untaken_with_liquidity | 2026-08-06 15:09:39 UTC | 1.7621 | 343 | 434 | 666 | 666 | 3 | 303.3 | True |
| GOOGLx | untaken_with_liquidity | 2026-08-05 08:01:26 UTC | 1.1258 | 3384 | 3384 | 3384 | 3384 | 1 | 224.4 | True |
| HOODx | untaken_with_liquidity | 2026-08-05 06:10:18 UTC | 1.0278 | 875 | 2051 | 6560 | 6560 | 10 | 225.6 | True |
| CRCLx | untaken_with_liquidity | 2026-08-05 11:37:19 UTC | 1.0215 | 210 | 3249 | 5404 | 6578 | 12 | 406.8 | True |
| METAx | untaken_with_liquidity | 2026-08-05 16:11:39 UTC | 0.8760 | 270 | 1738 | 4339 | 4339 | 5 | 254.6 | True |
| HOODx | untaken_with_liquidity | 2026-08-05 05:54:49 UTC | 0.7647 | 292 | 1225 | 3048 | 5281 | 25 | 223.0 | True |
| CRCLx | untaken_with_liquidity | 2026-08-06 15:08:39 UTC | 0.6698 | 790 | 875 | 961 | 961 | 2 | 294.4 | True |
| GOOGLx | untaken_with_liquidity | 2026-08-05 16:00:59 UTC | 0.6089 | 173 | 1717 | 3916 | 4185 | 12 | 465.1 | True |
| GOOGLx | untaken_with_liquidity | 2026-08-06 15:10:58 UTC | 0.3011 | 892 | 892 | 892 | 892 | 1 | 220.7 | True |

## `pricing_anomaly` gate sensitivity

Rebuild all AMM $1,000 samples under tighter |AMM−CEX| gates and recompute portfolio single-flight capturable profit (same re-entry / trade duration as M0). Default gate in config is 500 bps.

| Gate (bps) | N samples | N windows | Portfolio profit (USDT) | $/day | Retained vs 500 |
|----------:|----------:|----------:|------------------------:|------:|----------------:|
| 500 | 62911 | 39 | 49.0568 | 24.0554 | 100.0% |
| 300 | 62762 | 35 | 27.3479 | 13.4103 | 55.7% |
| 200 | 61442 | 0 | 0.0000 | 0.0000 | 0.0% |
| 100 | 50028 | 0 | 0.0000 | 0.0000 | 0.0% |

## Conclusion

Over the available raw journal span (2026-08-05 02:12:00 UTC → 2026-08-07 03:08:37 UTC), portfolio single-flight capturable profit at AMM $1,000 / T=0 is **49.0568 USDT** (24.0554 USDT/day), across **39** windows. The top 20 windows carry **101.0%** of that portfolio total.

Classification of the top 20: **2 taken**, **18 untaken-with-liquidity**, **0 untaken-too-thin**. On-chain matching swaps validate **4.5%** of top-20 paper profit and **4.6%** of the full portfolio headline. Journal swap activity in-span is sparse (17 swaps total) — most large paper windows had **nobody** trading the pool in the profitable direction while the edge was open.

Of 18 untaken-with-liquidity top windows, median pool-join ages range 434–3384 ms (max ages up to 8187 ms; align gate 15000 ms). Ages sit well inside the align gate, so the join itself is fresh; the untaken status is more consistent with a real but uncontested (or risk-blocked) dislocation than with a stale pool snapshot.

Tightening `pricing_anomaly` from 500 → 300 / 200 / 100 bps retains **55.7% / 0.0% / 0.0%** of the 500-bps portfolio profit. A large drop under a tighter gate means the go-case rests on quotes the tooling itself nearly rejects; a small drop means the headline is robust to basis scrubbing.

**Implication for the bot go-case:** treat only the **taken** share as hard evidence that large dislocations were real and firm. Untaken-with-liquidity windows need a human explanation (MM risk limits, gas, inventory, or residual join artifact) before sizing; untaken-too-thin windows must not count toward live inventory allocation. Re-run this script after ≥5 clean RTH sessions so the original M0 concentration claim (two HOODx windows ≈ 98% of HOODx profit) can be re-checked on a longer raw span — raw `bybit_book` / `bybit_depth` retention is ~2 days, so the original 2026-08-03→05 M0 study ticks are mostly pruned.

## References

- `docs/references/m8-xstocks-edge-quant.md` (M0 go/no-go)  
- Phase-1 prior art: `src/mba/m6_attribution.py`, `docs/references/mm-attribution-analysis.md`  
- Bot DESIGN §8 (sibling `mantle-stocks-arbitrage-bots`)

*Companion JSON: `docs/references/m8-onchain-fill-validation.json` (schema version 1).*
