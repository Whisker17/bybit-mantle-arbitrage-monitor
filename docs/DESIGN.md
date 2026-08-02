# bybit-mantle-arbitrage-monitor — Design Document / PRD

> Spec of record for this project. Product decisions below were confirmed
> 2026-08-01 and written back to Linear project
> *Mantle <> Bybit Arbitrage Monitor* (`d120c58e-538a-40e6-8c25-f43c59b9335b`).
> Do not re-derive parameters or re-open §7 rejections without flagging.

## 1. Background & Goals

### 1.1 Vision

A **read-only** live panel that shows whether paper arbitrage between Bybit spot
and Fluxion (Mantle RWA DEX) is open on tokenized US equities (xStocks), for how
long, after realistic costs, and who is moving the prices.

### 1.2 v1 Scope

- **Pairs:** fixed list of Fluxion-liquid xStocks (~10); exact set locked in M1.
- **Price bases:** AMM pool quote **and** RFQ quote as **separate columns**
  (Fluxion = V2/V3 AMM + xChange Atomic RFQ). **RFQ quotes remain live for
  liquid pairs outside US RTH** (weekend two-sided quotes track Bybit mid;
  see `docs/references/m4-closed-session-rfq.md` / WHI-753). Do not treat
  closed hours as “AMM-only pricing.” Fill-volume regime open vs closed is
  still measured from on-chain LOP + Swap, not inferred from the wall clock.
- **Data:** pure realtime, accumulate from zero — **no historical backfill**.
- **Arb definition:** two-sided inventory paper arb. Wear =
  Bybit taker **0.10%** + Fluxion pool fee + Mantle gas + bilateral slippage.
- **Attribution:** mechanism layer (RFQ = MM-driven / AMM = active taker) +
  behavior layer (address heuristics; see §4.2 reuse of `m6_attribution`).
- **Stats:** all metrics segmented by **US equity open vs closed** session.
- **UI:** **TUI first (Python)**; Web panel (Next.js static export + FastAPI
  read-only API, all on VPS) lands as post-M5 work (WHI-757+). TUI is
  **frozen** once Web ships features — keep TUI usable, no new TUI metrics.

Phase-1 WMNT/USDT0 offline backtest remains in-tree under `src/mba/` + `report/`
(tag `phase1-backtest`) as an archived POC; it is not the product.

### 1.3 Non-goals (explicitly out of scope for v1)

- Order placement, trade execution, or any private exchange API that can trade.
- Triangular / multi-hop routing.
- MEV, gas-auction, or frontrun modeling as a product feature.
- Historical backfill of Bybit or Fluxion tapes.
- Venues beyond Bybit spot and Fluxion (Agni/Moe WMNT work stays in `src/mba` only).

### 1.4 Success criteria

- Live TUI shows per-symbol AMM + RFQ (or RFQ-degraded) mid/spread vs Bybit mid
  with cost-adjusted paper edge, session-segmented aggregates.
- M0–M5 Linear issues Done; Web skeleton (WHI-757) + overview (WHI-758) +
  pair detail (WHI-759) serve from the VPS; further metrics follow (WHI-756+).
- Phase-1 pipeline still regenerates `report/` from local `data/` parquet.

## 2. Requirements / Specification

### 2.1 Symbols

Fixed list, Fluxion-liquid xStocks. M1 (WHI-730) inventories on-chain pools +
Bybit spot symbols (including **multiplier** mapping — Bybit xStocks prices must
be de-multiplied before comparison).

### 2.2 Price columns

| Column | Source | Notes |
|--------|--------|-------|
| Bybit mid / L1 | public WS or REST | apply symbol multiplier |
| Fluxion AMM | on-chain pool quote (V2/V3) | contract quote preferred over reimplemented math |
| Fluxion RFQ | xChange Atomic RFQ public API (`pollable_quote`) | Live when HTTP 200 **and** `price` present (`FluxionRfqQuoteTick.available`); 204 / missing price = unavailable. Quotes can be two-sided on weekends (WHI-753). |

### 2.3 Paper edge (M3, live TUI path)

For size ladder \(Q\) (USD notionals; M3 ships $1K / $5K / $20K in
`config/metrics.yaml`):

```
edge_bps = direction_aware_spread_bps
         - bybit_taker_bps (10)
         - fluxion_fee_bps
         - bybit_slip_bps(Q)
         - fluxion_slip_bps(Q)
         - gas_bps(Q)
         - usdt_usdc_basis_bps   # optional; default 0
```

Inventory is pre-positioned on both sides (same model as phase-1); carry is an
aggregate cost, not per-fill amortization, unless revised with evidence.

**PnL v2** (below) is the cash-flow form of the same paper arb. It does **not**
replace the TUI’s M3 `edge_bps` path until a later issue wires it; implementers
must not assume `PnL_USD ≈ edge_bps/1e4 * Q` once multi-level VWAP / exact AMM
are on.

### 2.4 Session segmentation

All time-weighted stats split by US regular equity hours (exchange calendar TBD
in M3; default America/New_York RTH 09:30–16:00).

### 2.5 Attribution

1. **Mechanism:** RFQ prints → classify as MM-driven; AMM swaps → active taker.
2. **Behavior:** address heuristics imported from phase-1 M6 (contract vs EOA,
   entrypoint vs internal, clustering). Convergence-ratio rule of thumb from
   product discussion: ≥80% with ≥20 trades → arb-bot candidate (validate in M4).

### 2.6 PnL v2 (cash-flow paper arb — WHI-754 research, WHI-756 engine)

Spec of record for **USD PnL at size**, optimal-size search, and the fixed
bucket table. Methodology derivation and Hummingbot comparison:
`docs/references/hummingbot-pnl.md`.

#### 2.6.1 Product invariants

- **Two-sided inventory paper arb** (same as §1.2 / §2.3): no transfer cost,
  no wallet budget checker. Thin book / AMM range exhaust → `fillable=false`.
- **Size variable \(Q\)** (AMM path) = single-trade USD notional at
  **de-multiplied** Bybit mid. Matched base \(q\) is identical on both legs
  **after** Bybit fee rules (§2.6.2); see research note §4.2–4.4.
- **Directions** (same tokens as M3 `edge.py`):
  `buy_fluxion_sell_bybit`, `buy_bybit_sell_fluxion`.
- **Fluxion venues:** AMM (full size continuum + optimal search) and RFQ
  (**poll-native rows only** — not the AMM bucket ladder; rate limit).
- **Negative PnL is first-class** (gas-dominated micro buckets); never clamp to 0.

#### 2.6.2 Cash-flow formulas

Bybit taker fee \(f_b = 10\,\mathrm{bps}\) (config). Spot fees are charged in the
**received** asset (Bybit help center): buy → fee in base; sell → fee in quote.
VWAP levels: \(p = p^{\mathrm{raw}}/m\), \(s = s^{\mathrm{raw}}\cdot m\) (notional
invariant; matched base \(q\) shares that unit). Full algebra in research note
§4.2.

| Direction | Buy leg (trader pays) | Sell leg (trader receives) | PnL (USD) |
|-----------|----------------------|----------------------------|-----------|
| `buy_fluxion_sell_bybit` | Fluxion: USDC spent to acquire net base \(q\) (fee-inclusive AMM; or RFQ `amountIn`) | Bybit: sell \(q\) at bid VWAP; USDT received after fee | \(\mathrm{USDT_{recv}} - \mathrm{USDC_{spent}} - G - \beta Q\) |
| `buy_bybit_sell_fluxion` | Bybit: buy gross base so **net** base \(= q\) after fee; USDT spent | Fluxion: sell \(q\) for USDC received (fee-inclusive AMM; or RFQ `amountOut`) | \(\mathrm{USDC_{recv}} - \mathrm{USDT_{spent}} - G - \beta Q\) |

- \(G =\) `gas_usd_per_swap` (default $0.01), charged **once** per Fluxion leg
  (AMM and RFQ; default charge gas on RFQ too — conservative).
- USDT/USDC cash legs 1:1; \(\beta = \texttt{usdt\_usdc\_basis\_bps}/10^4\)
  (default 0) is **additive wear on both directions** (same contract as M3
  `edge.py`). Error if left at 0 while true basis ≠ 0 is typically sub-5 bps
  (DESIGN §8).
- AMM fee: fee-inclusive amounts in cash-flow; UI wear breakdown may split fee
  vs impact **without** double-subtracting in \(\mathrm{PnL}\).
- RFQ: no separate pool-fee line. Rows are keyed by **poll size** (USDC
  EXACT_INPUT for buys; native base EXACT_INPUT for sells — matches
  `config/collector.yaml` today). Do not invent an RFQ impact curve for
  off-poll sizes; do not force RFQ onto the AMM \(Q\) grid (research note §4.3.2).

#### 2.6.3 Size ladders: M3 vs PnL v2

| Path | Sizes | Owner |
|------|-------|-------|
| M3 TUI `edge_bps` (§2.3) | **$1 000 / $5 000 / $20 000** (`config/metrics.yaml`) | Live panel today |
| PnL v2 AMM buckets + search (this section) | **$10 / $50 / $100 / $500 / $1 000 / $10 000** | WHI-756 engine; Web/API WHI-766 |
| PnL v2 RFQ | Collector poll notionals only (not the AMM bucket list) | WHI-756 / WHI-766 |

PnL v2 **does not** change the M3 TUI ladder. The v2 bucket list feeds the
cash-flow engine (WHI-756) and Web/API serialization (WHI-766). M3
`OverviewModel` / net-edge path stays on $1K/$5K/$20K.

#### 2.6.4 Optimal size (AMM only)

\[
Q^\star = \arg\max_Q \mathrm{PnL}(Q)
\quad Q \in [Q_{\min}, Q_{\max}]
\]

with \(Q_{\max} = \min(\mathrm{config\_cap}, \mathrm{depth\_cap}, \mathrm{amm\_cap})\).

**Search (normative):** log-spaced coarse grid → local peak brackets → linear
refine → force-evaluate endpoints. **Do not assume concavity**; multi-peak
piecewise books are possible (Bybit steps + V3 range). Defaults and termination
in `docs/references/hummingbot-pnl.md` §5. Claim: best among evaluated samples,
not a proven continuous global max.

#### 2.6.5 Guardrails (from Hummingbot, panel-shaped)

| Guard | Rule |
|-------|------|
| Freshness | Bybit / pool / RFQ age caps; stale → unfillable + reason |
| Align | Dual-leg snapshot skew ≤ `align_skew_ms` |
| Min profit | Config threshold for **highlight / breach only** — raw PnL always emitted |
| Thin book | Partial depth fill ⇒ unfillable (no silent partial) |

#### 2.6.6 Engine ownership

| Piece | Issue |
|-------|-------|
| This research + DESIGN §2.6 | **WHI-754** (landed with the research note) |
| Pure metrics engine + tests + bucket/optimal API | **WHI-756** (landed: `monitor/metrics/pnl_v2.py`, `config/metrics.yaml` `pnl_v2:`, CLI `python -m monitor.metrics`) |
| Live Bybit multi-level depth journal | **WHI-755** (`bybit_depth` precomputed VWAPs @ PnL buckets; L1 still `bybit_book`) |
| Engine consumes depth / L1 fallback | **WHI-756** (landed on the pure path: optional `bybit_bids`/`bybit_asks` → base-sized VWAP; L1 when omitted). M3 `compute_edge` TUI path remains L1 (see `docs/DEFERRED_ISSUES.md`) |
| Web/API serialization + UI | **WHI-766** (landed: `pnl_snapshot.py`, overview `pnl_v2` optimal card, detail dual-direction tables; `config/api.yaml` `pnl_cache_ttl_s`) |

#### 2.6.7 API cache (WHI-766)

Optimal-size search evaluates ~40 AMM samples per direction. To keep overview
polls under ~500 ms P95 on the 1 GB VPS:

- Process-local TTL cache (`monitor.api.pnl_cache.PnlSnapshotCache`) keyed by
  `pair_id`, holding the full dual-direction `PnlPairSnapshot`.
- TTL default **`pnl_cache_ttl_s: 2.5`** in `config/api.yaml` — slightly above
  `poll_interval_s: 2.0` so a steady poller still hits cache on the next tick,
  and concurrent overview + detail polls share one compute.
- Set `pnl_cache_ttl_s: 0` to recompute every request (tests / debugging).
- Single uvicorn worker is assumed (same as WHI-757); cache is not shared
  across workers.
- Depth: journal `bybit_depth` notional VWAP curves are reconstructed into
  stepwise levels via cumulative base qty \(Q_i/V_i\) so sloped books round-trip
  correctly. Missing depth → overview status `no_depth` (detail still shows L1
  tables).
- P95 target: keep overview+detail under ~500 ms with this cache; re-measure on
  first VPS deploy (pair count × optimal samples under `state.lock`).

### 2.7 Multi-market metrics & attribution (WHI-773 / M7-4)

**Invariant:** one metrics/attribution code path for every market. Venue
parameters are injected at assembly time (`monitor.markets.MarketContext`);
algorithms under `monitor.metrics` / `monitor.attribution` stay market-agnostic.

| Concern | Source | Notes |
|---------|--------|-------|
| CEX taker fee | market `costs.cex_taker_fee_bps` → `MetricsConfig.bybit_taker_fee_bps` | Field name is historical; value is the active CEX venue fee (Bybit 10, Binance 10). |
| Gas per AMM swap | market `costs.gas_usd_per_swap` | Mantle ~$0.01; BSC inventory default $0.05 (non-zero constant). |
| Quote basis wear | market `costs.quote_basis_bps` → `usdt_usdc_basis_bps` | 0 when CEX and DEX share the same quote (Binance USDT ⇄ Pancake USDT). |
| Pool fee | inventory per-pool `amm.fee` (UniV3 units) | Injected into `AmmPoolState.pool_fee` at tick lift — not a global YAML. |
| CEX depth VWAP | journal `bybit_depth` (table name reused per ADR-0001) | M7-3 Binance depth20 precomputes the same bucket curve shape. |
| Multiplier / comparable mids | collector writes `*_de_multiplied` | **divide** (Bybit xstock) vs **multiply** (BEP-677 uiMultiplier). Metrics always consume comparable columns; do not re-apply the formula. Historical series keep the mult stamped on each tick (no retroactive rebase of the journal). |
| Session open/closed | shared NYSE calendar (`metrics.session`) | Same for both markets (US equity underlyings). |
| Cumulative distributions | per-market SQLite | `data/monitor-{market_id}.db` isolates “since go-live” stats. |
| Mechanism RFQ/AMM | `AttributionConfig.has_rfq` from `dex.has_rfq` | Config switch via `apply_market_attribution` — **not** `if market_id == …`. When false, RFQ fills are dropped and mechanism share is 100% AMM (Pancake). |
| Behavior labels | shared `config/attribution.yaml` thresholds | Per-market retune: pass `attribution_path` into `load_market_context`. MM `market_maker` rules that require RFQ maker fills stay inactive without RFQ data. |

**CLI:** `python -m monitor.metrics --market binance-pancake` uses market costs
and optional inventory pool fee when `--pair-id` matches. Synthetic demo only
(no journal); Web multi-market surface is M7-5.

**Pool geometry:** pure `monitor.metrics.amm_pool.amm_pool_from_tick` takes
explicit quote/base decimals (USDC=6 on Mantle, USDT=18 on BSC). TUI/API pair
wrappers accept `Pair` or `BStocksPair`.

## 3. Cross-cutting Policies

- **No secrets in git.** RPC keys and Linear API keys only in `.env`.
- **No trading credentials** in this repo for v1.
- **High-risk paths** (human review on agent PRs): key handling, RPC credentials.
- **Git:** one issue = one worktree off `origin/dev` = one PR into `dev`
  (`docs/GIT_WORKFLOW.md`).

## 4. System Architecture

### 4.1 Tech stack

| Choice | Why |
|--------|-----|
| Python ≥3.11 + uv | matches phase-1; fast iteration for TUI |
| httpx | RPC + REST (existing `mba.rpc` pattern) |
| polars / duckdb | local analytics if needed; TUI may stay in-memory |
| **Textual** (TUI) | M5 chose Textual over rich for interactive two-level nav (DataTable + detail screen); pure view models stay library-free |
| **FastAPI** (read-only API) | WHI-757: serves overview/detail/trades/health JSON over collector SQLite; reuses TUI builders + M3/M4 |
| **Next.js static export** | WHI-757: build on laptop/CI, rsync `web/out` to VPS; no Node runtime on the 1GB box |
| nginx + systemd | VPS: static site + `/api` reverse-proxy; `xstocks-api.service` (uvicorn, 1 worker, MemoryMax) |
| Mantle JSON-RPC + Multicall3 | pool state + eth_call quotes |

### 4.2 Module layout

```
src/
  mba/          # PHASE-1 ARCHIVE — do not extend for xStocks; keep runnable
  monitor/      # PHASE-2 product code (all new work here)
```

Planned `src/monitor/` packages (land with their issues; empty package until then):

| Module (planned) | Responsibility | First issue |
|------------------|----------------|-------------|
| `monitor/markets` | market id, inventory/costs assembly, per-market SQLite paths | M7-2 (landed WHI-771) |
| `monitor/symbols` | fixed xStock list, Bybit multiplier map | M1 |
| `monitor/bybit` | live Bybit book/trades WS; orderbook.50 + `bybit_depth` VWAP | M2 (WHI-731); depth WHI-755 |
| `monitor/binance` | live Binance bookTicker / depth20 VWAP / aggTrade (bStocks) | M7-3 (landed WHI-772) |
| `monitor/fluxion` | AMM state/quotes + RFQ feed; also BSC Pancake poll (parameterized) | M2 (landed WHI-731); M7-3 WHI-772 |
| `monitor/storage` | SQLite journal for collector ticks + retention | M2 (landed WHI-731); retention WHI-751 |
| `monitor/collector` | daemon orchestrating feeds → SQLite (+ retention loop); multi-market | M2 (landed WHI-731); M7-3 WHI-772 |
| `monitor/retention` | thin CLI over `storage.retention` (`python -m monitor.retention`) | WHI-751 |
| `monitor/metrics` | edge, wear, session stats | M3 (landed WHI-732) |
| `monitor/attribution` | mechanism + behavior labels (+ MM/rebalancer, WHI-768) | M4 (landed WHI-733); MM productization WHI-768 |
| `monitor/tui` | live panel (Textual overview + detail); **frozen** after Web lands | M5 (landed WHI-734) |
| `monitor/api` | read-only FastAPI over the same journal + builders | WHI-757 (skeleton) |
| `web/` | Next.js static export (panel UI) | WHI-757 skeleton; WHI-758 overview; WHI-759 pair detail |
| `deploy/` + `scripts/deploy-web.sh` | systemd unit, nginx site, one-command redeploy | WHI-757 |

#### Phase-1 → phase-2 reuse map

Do **not** import `mba` stages as a whole. Lift or re-home the pieces below into
`monitor/` (or a thin shared module) when the consuming milestone starts. Source
of truth until then is the path in the left column.

| Reuse item | Phase-1 source | Phase-2 consumer | What to take |
|------------|----------------|------------------|--------------|
| RPC + Multicall3 | `mba/rpc.py` (`Rpc`, rate-limit handling, `multicall` / `encode_call`) | M2 collector — per-block / per-poll pool state | Copy/adapt; drop archive-specific resume if unused |
| V3 venue encoding | `mba/venues.py` (`state_calls`, `quote_call`, Agni QuoterV2 struct form) + `mba/m2_quotes.py` pass structure | Fluxion V3 pool quote & slippage | **Keep contract-quoted path**; re-verify Fluxion is not a silent topic0 mismatch (phase-1 lesson: Agni ≠ vanilla UniV3) |
| Bybit fee & slip model | `mba/config.py` `BYBIT_TAKER_FEE`, `mba/m3_bybit.py` VWAP depth + mid-based slip (warn: slip is from mid, includes half-spread), `mba/m4_align.py` `slip_fraction` / hurdle | M3 wear | Reuse math; **transport** REST/CSV → WS rewrite in M2 |
| Taker classification heuristics | `mba/m6_attribution.py` `classify_addresses` (eth_getCode), `probe_roles` (entrypoint vs internal), direction-aware swap decode, clustering / top-beneficiary breakout | M4 attribution starting point | Heuristics + thresholds; re-tune on xStock flow |

`src/mba` stays in place. Archive or delete only after phase-2 is stable (separate
decision, not M0).

### 4.3 Key interfaces

| Type / config | Module | Notes |
|---------------|--------|-------|
| `MarketContext` / market file | `monitor.markets` | Explicit market assembly (M7-2): id, cex/dex, costs, inventory path, per-market SQLite (ADR-0001) |
| `PairsConfig` / `Pair` | `monitor.symbols` | Bybit ⇄ Fluxion inventory from `config/markets/bybit-fluxion.yaml` `inventory:` (M1; path moved M7-2) |
| `BStocksPairsConfig` / `BStocksPair` | `monitor.symbols` | Binance ⇄ Pancake inventory from `config/markets/binance-pancake.yaml` `inventory:` (M7-3) |
| `de_multiplied_price` | `monitor.symbols` | `bybit_mid / xstock_multiplier` before venue compare (**divide** semantics — not for bStocks) |
| `multiplied_price` | `monitor.symbols` | `binance_display * ui_multiplier` for raw/on-chain compare (**multiply** / BEP-677) |
| RFQ mode | market inventory `rfq.mode` | **`pollable_quote`** on bybit-fluxion (M1); binance-pancake has no RFQ |
| `BybitBookTick` / `BybitTradeTick` | `monitor.quotes` | CEX L1 + trades journal shape (Bybit or Binance; per-market SQLite) |
| `FluxionPoolStateTick` / `FluxionSwapTick` | `monitor.quotes` | Per-block AMM mid (wrapper + native) + swaps |
| `FluxionRfqQuoteTick` / `FluxionRfqFillTick` | `monitor.quotes` | Pollable RFQ quote + LOP settlement events |
| `CollectorConfig` | `monitor.collector` | `config/collector.yaml` tunables (v2 per-market sections) |

Strategy/metrics code should depend only on `monitor.quotes` shapes, not on WS/RPC
client internals.

### 4.4 Core flows

1. **Boot** → load symbol list + multiplier map → open Bybit WS + Fluxion poll/WS.
2. **On tick** → update mid/L1 → recompute paper edge per size → push TUI model.
3. **On chain event / RFQ print** → attribution classifier → session buckets.
4. **Session roll** → close open-bucket stats, open next.

### 4.5 State & recovery

Collectors start from the live tip (no historical backfill). Restart leaves prior
SQLite rows in place but does not re-fetch missed wall-clock gaps — reconnects and
long block lag write explicit rows to `collector_gaps` and set per-row `gap=1` on
the next ticks. Local journal: **one SQLite file per market**
`data/monitor-{market_id}.db` via `monitor.storage.SqliteStore` (M2 / WHI-731;
per-market split M7-2 / WHI-771, ADR-0001). Default market `bybit-fluxion`.
TUI (M5) may still keep an in-memory view derived from the same ticks.

Mantle settled head uses `head_lag_blocks: 1` by default (WHI-749): process
`eth_blockNumber() - 1` so load-balanced RPC read-your-writes does not emit
recurring `"block N not found"` gaps. See §5.2.

## 5. Data & Observability

- TUI is the primary observability surface in v1.
- Structured logs for feed disconnects / RPC errors on stderr from
  `python -m monitor.collector`; gap rows in SQLite `collector_gaps`.
- Collector SQLite `meta` exposes `last_block_ingest_latency_ms` plus rolling
  `block_ingest_latency_{p50,p95,p99}_ms` (WHI-749). Prefer percentiles over a
  single smoke `last_*` sample.
- No production PagerDuty-style alerting in v1.

### 5.1 Journal retention (WHI-751)

The live collector appends to `data/monitor.db` indefinitely unless pruned.
On the deploy VPS (shared ~disk with other bots; free space observed ~5.2 GiB
at ticket open), **unbounded growth exhausts the disk in weeks**.

#### Growth model (ungoverned)

| Regime | Observed / assumed write rate | Dominant table | Approx size |
|--------|-------------------------------|----------------|-------------|
| US closed / weekend quiet | ~7 L1 rows/s all pairs (when L1 changes) | `bybit_book` | ~150 MiB/day |
| US open (book hot) | 3–5× weekend (order-of-magnitude) | `bybit_book` | ~0.5–0.8 GiB/day |
| Depth journal (WHI-755) | ≤1 row/s/pair @ `emit_interval_ms: 1000` (~11 rows/s) | `bybit_depth` | ~0.2–0.4 GiB/day |

Derivation (weekend L1): \(7 \times 86400 \approx 6.0 \times 10^5\) rows/day; ~250 B/row
payload+index overhead → ~150 MiB/day. Depth: ~11 pairs × 86400 × ~300–400 B/row
(JSON VWAP curves) → ~0.25–0.35 GiB/day at 1 Hz throttle; mid-move flush adds little
on quiet books. At 0.5 GiB/day open L1 + depth + shared free 5.2 GiB
→ **disk full in ~1–2 weeks** without retention. Measure live with:

```bash
python -m monitor.retention --growth-only
```

#### Policy (config: `collector.yaml` → `retention`)

| Table | Default TTL | Rationale |
|-------|-------------|-----------|
| `bybit_book` raw | **2 days** | Bulk of bytes. TUI cold-start uses ≤`edge_history_max_samples` (2k) recent books; sparklines use ≤240 points. Daemon skips insert when L1 bid/ask unchanged (WHI-755). |
| `bybit_book_1m` | **14 days** | Before deleting raw books older than the raw TTL, last-in-minute L1 is upserted here (downsample). Compact series for forensics / future cold-start. |
| `bybit_depth` | **2 days** | Precomputed PnL-bucket VWAP curves (WHI-755). Throttled (`emit_interval_ms` + optional mid-move). Sized like a second bulk table — same raw TTL as L1 until growth re-measured. |
| `bybit_trades` | **7 days** | ≥ TUI 24h volume window (`volume_window_ms`). |
| `fluxion_pool_state` | **7 days** | Edge rebuild + sparklines. |
| `fluxion_rfq_quotes` | **3 days** | Poll tape; RFQ notional is small vs book. |
| `fluxion_swaps` | **permanent** | M4 attribution feedstock (low volume). |
| `fluxion_rfq_fills` | **permanent** | M4 attribution feedstock (low volume). WHI-768 enriches maker/taker/pair/amounts from receipts. |
| `erc20_transfers` | **permanent** | WHI-768 native xStock Transfer stream (inventory / rebalance feedstock; low volume). |
| `address_labels` | **permanent** | WHI-768 address → label + evidence (auto + manual override). |
| `rebalance_events` | **permanent** | WHI-768 CEX-touch deposit/withdraw stream. |
| `collector_gaps` | **30 days** | Ops history. |

**M3 cumulative P50/P95/P99/max + breach stats** live in process memory
(`EdgeStats` / `RunningEdgeState`), not in SQLite. TUI cold-start rebuilds from
at most `edge_history_max_samples` recent journal books (documented in
`config/tui.yaml`). Pruning raw books older than the raw TTL therefore **does
not change the live aggregate definition** once the process is warm; after
restart, cold-start still sees the same capped sample budget as before.
Attribution reads swaps/fills, which are never pruned by default.

Steady-state bound (order of magnitude, ~10 pairs):

- raw book ≈ 2 × 150 MiB–0.8 GiB ≈ **0.3–1.6 GiB**
- depth curves ≈ 2 × 0.25–0.4 GiB ≈ **0.5–0.8 GiB** (1 Hz throttle)
- 1m bars ≈ 10 pairs × 14 d × 1440 min × ~120 B ≈ **~25 MiB**
- trades + pool + RFQ quotes (TTL windows) + permanent swaps/fills ≪ book

→ **steady-state journal ≪ free disk** when TTLs hold. Re-measure after
deploy with `python -m monitor.retention --growth-only` (WHI-755 AC).

#### Runtime

1. **In-process loop** in `monitor.collector` (`retention.interval_s`, default 1h);
   first pass runs shortly after boot. No extra systemd unit required on the
   VPS; optional timer can still call the CLI.
2. **One-shot CLI** (manual / cron / timer): `python -m monitor.retention`
   (`--growth-only` for quantification without prune).
3. **DELETE** in rowid batches (`delete_batch_size`); each batch is its own
   short store-lock transaction so collector inserts interleave.
4. **Space reclaim:** `PRAGMA wal_checkpoint(TRUNCATE)` +
   `incremental_vacuum(N)` when the DB was created with
   `PRAGMA auto_vacuum=INCREMENTAL` (set automatically for **new** files in
   `SqliteStore`). Existing DBs stay at their original mode — freelist pages are
   still reused so size **plateaus** after the first full prune cycle; run once
   with `--full-vacuum` (or critical waterline) to shrink the file on disk.
5. **Schema:** `SCHEMA_VERSION` bumps add tables via `CREATE IF NOT EXISTS`
   (no destructive migration). v2 = `bybit_book_1m`; v3 = `bybit_depth`
   (WHI-755); v4 = RFQ fill enrichment columns + `erc20_transfers` +
   `address_labels` + `rebalance_events` (WHI-768; ALTER ADD COLUMN for
   pre-v4 `fluxion_rfq_fills`). Meta key is updated for operators; readers
   do not gate on the integer.

#### Disk waterline (`retention.disk`)

| Level | Free space | Behavior |
|-------|------------|----------|
| ok | ≥ `warn_free_bytes` (2 GiB) | Config TTLs |
| warn | < warn, ≥ critical | Multiply pruneable TTLs by `warn_ttl_factor` (0.25) |
| critical | < `critical_free_bytes` (1 GiB) | Multiply by `critical_ttl_factor` (0.05); pause **new** `bybit_book` inserts until a later run clears critical; force `VACUUM` attempt |

`fluxion_swaps` / `fluxion_rfq_fills` / `erc20_transfers` / `rebalance_events`
TTLs are **never accelerated** (only an explicit non-null TTL in config would
prune them).

### 5.2 Mantle block ingest latency (WHI-749)

**Metric** (matches collector meta and probe):

```
latency_ms = max(0, recv_ts_ms - block_ts * 1000)
```

`block_ts` is the on-chain block timestamp; `recv_ts_ms` is local wall clock
**after** all per-block RPC (getBlock + Multicall3 + pool/LOP logs + optional
swap receipts + WHI-768 RFQ receipt enrich when fills present + optional
native Transfer `getLogs`). The value therefore includes host clock skew vs
chain time, tip visibility on the RPC LB, and processing — not “RPC RTT alone.”
Transfer stream + RFQ enrich are config-gated (`mantle.collect_erc20_transfers`,
`mantle.enrich_rfq_fills`); re-measure P95 after enabling on the VPS if the
latency SLO is tight.

**Defaults** (`config/collector.yaml`):

| Knob | Default | Why |
|------|---------|-----|
| `mantle.head_lag_blocks` | **1** | Cut lag=0 catch-up P95 tails; +~2 s systematic lag (transient not-found still possible) |
| `mantle.block_poll_interval_s` | **0.25** | Measured sweet spot; 0.10 s increases not-found retries |

**Acceptance (keyed Mantle RPC, steady state after warmup):**

| Criterion | Target |
|-----------|--------|
| `block_ts → recv` P95 | **&lt; 15 s** (keyed, steady) |
| Per-block RPC work (`rpc_work`) P95 | **&lt; 3 s** |
| Continuity | No `skipping to tip` under steady poll; block series contiguous |
| `"block N not found"` | May be transient on LB even at lag=1; retries same N, sets `gap=1`; **not** a hard 30 min zero — see research note annotation strategy |

Public `https://rpc.mantle.xyz` is degrade-mode only (worse freshness; no hard
P95). Original WHI-731 wording “P95 &lt; 2 s” is **superseded** — unreachable
once skew + `head_lag=1` + 8-pool Multicall are included in the metric.
Full measurement tables: `docs/references/m2-block-ingest-latency.md`.
Probe: `python -m monitor.collector.latency_probe`.

## 6. Milestones

| ID | Linear | Success criterion |
|----|--------|-------------------|
| **M0** | WHI-736 | Three-commit history (phase1 tag → template → monitor skeleton); phase-1 pipeline still runs; DESIGN has reuse map |
| **M1** | WHI-730 | Symbol list + Fluxion/Bybit inventory + RFQ API feasibility + multiplier map |
| **M2** | WHI-731 | Live collectors for Bybit + Fluxion AMM (+ RFQ or degraded); block ingest SLO in §5.2 (WHI-749 revised) |
| **M3** | WHI-732 | Edge/wear metrics + session segmentation |
| **M4** | WHI-733 | Attribution (mechanism + heuristics) |
| **M5** | WHI-734 | TUI panel |
| **M6** | WHI-735 | ~~Web panel plan doc only~~ **Canceled** 2026-08-01 — replaced by implementable Web issues |
| **Web skeleton** | WHI-757 | FastAPI read-only API + Next.js static export + nginx/systemd deploy on VPS |
| **Web overview** | WHI-758 | Full overview table (TUI-parity columns, status/stale banner, sort/filter) |
| **Web pair detail** | WHI-759 | Pair detail: spread chart, trade stream, edge stats, attribution |
| **M7 multi-market (Binance ⇄ Pancake bStocks)** | WHI-770… | Second market beside Bybit⇄Fluxion. **M7-1…M7-3** landed (inventory, domain, collectors). **M7-4 (WHI-773) landed:** same M3/M4/PnL v2 code path for `binance-pancake` — market cost injection, BEP-677 comparable mids, `has_rfq` mechanism degeneration, CLI `--market`, DESIGN §2.7. Web multi-market bar **M7-5**. |

Dependency chain: M0 → M1 → M2 → (M3 ∥ M4) → M5 → Web (WHI-757 → 758…).
M7 is parallel product expansion after Web PnL v2; does not block Web polish.

## 7. Rejected Alternatives

| Option | Why rejected |
|--------|----------------|
| New repo for phase-2 | Zero-commit state made overlay ≡ template; Linear project already points at `report/`; avoid moving 3k LOC |
| Continue WMNT/USDT0 product | User rejected phase-1 POC product direction 2026-08-01 |
| Web-first UI (v1) | TUI first for operator speed; Web deferred until after M5, then full VPS stack (WHI-757) instead of plan-only M6 |
| Vercel / hosted Web + data egress | Operator wants data on-box; 1GB VPS already runs nginx; static export + FastAPI co-located with collector SQLite |
| Historical backfill for live panel | Product is forward-looking paper arb, not another backtest |
| Single blended AMM+RFQ price | Must show both; quote availability and spreads still differ by pair and session, but closed ≠ RFQ-off (WHI-753) |
| Auto-trade / bot execution | Explicit non-goal |

## 8. Known Risks & Open Questions

| Risk / question | Owner |
|-----------------|-------|
| xChange RFQ **public quote** API may not exist → degrade to last RFQ fill | **Resolved M1:** public EXACT_INPUT quote is pollable; see `docs/references/m1-rfq-feasibility.md` |
| Closed hours assumed RFQ-dark / AMM-only pricing | **Resolved WHI-753:** liquid pairs still quote two-sided RFQ on weekends and track Bybit mid; see `docs/references/m4-closed-session-rfq.md`. Open-vs-closed *fill* rates still open. |
| Bybit xStocks **multiplier** must be applied or edges are nonsense | **Resolved M1:** `instruments-info.xstockMultiplier` + `de_multiplied_price`; snapshots in `config/pairs.yaml` |
| bStocks (Binance) multiplier may not match Bybit semantics | **Resolved WHI-770 (inventory):** BEP-677 `uiMultiplier` scales **UI qty**, raw `balanceOf` unchanged — not classic rebase. Binance public `exchangeInfo` has no mult field. Pricing identity: `amm_raw_mid ≈ binance_display_mid * (uiMultiplier/1e18)` (**multiply**, opposite direction from Bybit divide). Draft field `ui_multiplier` in `config/binance_pancake_pairs.yaml` — do not reuse `de_multiplied_price` unchanged. |
| US VPS cannot reach `api.binance.com` for M7 collector | **Resolved WHI-770 (measured):** `107.175.234.202` gets HTTP **451** on api/stream.binance.com; **`data-api.binance.vision` + `data-stream.binance.vision` return 200/101**. Default M7-6 path: vision endpoints on existing VPS; non-US sidecar only if vision gaps. |
| Fluxion pool ABI / fork lineage unknown until M1 (phase-1 Agni topic0 trap) | **Resolved M1:** UniV3-lineage factory/quoter; liquid xStock pools fee=3000 USDC. M2 still re-verifies topic0 on live swaps |
| Bybit quote is **USDT** while Fluxion AMM/RFQ quote is **USDC** — basis not modeled in M1 | M3 (knob `usdt_usdc_basis_bps`, default 0); PnL v2 same default — measure before production accuracy claims |
| Live book depth quality vs phase-1 single snapshot approximation | M2/M3; PnL v2 VWAP needs depth (L1 degrade until wired) |
| PnL v2 optimal search is sample-best, not continuous global max | WHI-756; acceptable for panel buckets $10–$10k |
| Mantle block ingest P95 / head_lag (LB not-found) | **Resolved WHI-749:** default `head_lag_blocks: 1`; SLO in §5.2; note `docs/references/m2-block-ingest-latency.md` |
| Heuristic thresholds (80% / 20 trades) unvalidated on xStocks | M4 |
| TUI library choice (textual vs rich) | **Resolved M5:** Textual |
