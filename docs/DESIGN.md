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
- **UI:** **TUI first (Python)**. Web panel is plan/docs only in v1 (M6).

Phase-1 WMNT/USDT0 offline backtest remains in-tree under `src/mba/` + `report/`
(tag `phase1-backtest`) as an archived POC; it is not the product.

### 1.3 Non-goals (explicitly out of scope for v1)

- Order placement, trade execution, or any private exchange API that can trade.
- Triangular / multi-hop routing.
- MEV, gas-auction, or frontrun modeling as a product feature.
- Web UI implementation (plan only).
- Historical backfill of Bybit or Fluxion tapes.
- Venues beyond Bybit spot and Fluxion (Agni/Moe WMNT work stays in `src/mba` only).

### 1.4 Success criteria

- Live TUI shows per-symbol AMM + RFQ (or RFQ-degraded) mid/spread vs Bybit mid
  with cost-adjusted paper edge, session-segmented aggregates.
- M0–M5 Linear issues Done; M6 produces a Web plan doc only.
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
bucket table. Methodology derivation and Hummingbot对照:
`docs/references/hummingbot-pnl.md`.

#### 2.6.1 Product invariants

- **Two-sided inventory paper arb** (same as §1.2 / §2.3): no transfer cost,
  no wallet budget checker. Thin book / AMM range exhaust → `fillable=false`.
- **Size variable \(Q\)** = single-trade USD notional at **de-multiplied** Bybit mid.
  Base amount \(q = Q / P_b^{\mathrm{mid}}\) is identical on both legs.
- **Directions:** `buy_fluxion_sell_bybit` (BFSB), `buy_bybit_sell_fluxion` (BBSF).
- **Fluxion venues:** AMM (full size continuum) and RFQ (**reference poll sizes
  only** — rate limit; not a dense RFQ ladder).
- **Negative PnL is first-class** (gas-dominated micro buckets); never clamp to 0.

#### 2.6.2 Cash-flow formulas

Bybit taker fee \(f_b = 10\,\mathrm{bps}\) (config). Levels for VWAP are
**de-multiplied prices** with base sizes; multiplier is applied at tick ingest,
not inside PnL.

| Direction | Buy leg | Sell leg | PnL (USD) |
|-----------|---------|----------|-----------|
| BFSB | Fluxion: USDC in to buy \(q\) (fee-inclusive AMM or RFQ) | Bybit: sell \(q\) at bid VWAP, net \(\times(1-f_b)\) | \(\mathrm{usd}(\mathrm{USDT_{in}}) - \mathrm{usd}(\mathrm{USDC_{out}}) - G\) |
| BBSF | Bybit: buy \(q\) at ask VWAP, net \(\times(1+f_b)\) | Fluxion: sell \(q\) for USDC (fee-inclusive) | \(\mathrm{usd}(\mathrm{USDC_{in}}) - \mathrm{usd}(\mathrm{USDT_{out}}) - G\) |

- \(G =\) `gas_usd_per_swap` (default $0.01), charged **once** per Fluxion leg
  (AMM and RFQ; config `gas_on_rfq`, default true).
- USDT/USDC: default 1:1 (\(\beta=0\)); optional basis via existing
  `usdt_usdc_basis_bps` — error declaration: untreated basis is typically
  sub-5 bps and remains DESIGN §8 open until measured.
- AMM fee: fee-inclusive amounts in cash-flow; UI wear breakdown may split fee
  vs impact **without** double-subtracting in \(\mathrm{PnL}\).
- RFQ: no separate pool-fee line; size ≠ poll notional → `n/a` or
  `rfq_size_mismatch` flag (no invented impact curve).

#### 2.6.3 Fixed buckets and optimal size

Display / engine buckets (USD): **10 / 50 / 100 / 500 / 1 000 / 10 000**.

For AMM (each direction), also compute

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

#### 2.6.4 Guardrails (from Hummingbot, panel-shaped)

| Guard | Rule |
|-------|------|
| Freshness | Bybit / pool / RFQ age caps; stale → unfillable + reason |
| Align | Dual-leg snapshot skew ≤ `align_skew_ms` |
| Min profit | Config threshold for **highlight / breach only** — raw PnL always emitted |
| Thin book | Partial depth fill ⇒ unfillable (no silent partial) |

#### 2.6.5 Engine ownership

| Piece | Issue |
|-------|-------|
| This research + DESIGN §2.6 | **WHI-754** (landed with the research note) |
| Pure metrics engine + tests + bucket/optimal API | **WHI-756** |
| Live Bybit multi-level depth on the quote path | Deferred depth work (see `docs/DEFERRED_ISSUES.md`); engine accepts depth when present, L1 otherwise |

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
| `monitor/symbols` | fixed xStock list, Bybit multiplier map | M1 |
| `monitor/bybit` | live Bybit book/trades WS | M2 (landed WHI-731) |
| `monitor/fluxion` | AMM state/quotes + RFQ feed | M2 (landed WHI-731) |
| `monitor/storage` | SQLite journal for collector ticks + retention | M2 (landed WHI-731); retention WHI-751 |
| `monitor/collector` | daemon orchestrating feeds → SQLite (+ retention loop) | M2 (landed WHI-731); retention WHI-751 |
| `monitor/retention` | thin CLI over `storage.retention` (`python -m monitor.retention`) | WHI-751 |
| `monitor/metrics` | edge, wear, session stats | M3 (landed WHI-732) |
| `monitor/attribution` | mechanism + behavior labels | M4 (landed WHI-733) |
| `monitor/tui` | live panel (Textual overview + detail) | M5 (landed WHI-734) |

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
| `PairsConfig` / `Pair` | `monitor.symbols` | Fixed inventory from `config/pairs.yaml` (M1) |
| `de_multiplied_price` | `monitor.symbols` | `bybit_mid / xstock_multiplier` before venue compare |
| RFQ mode | `config/pairs.yaml` `rfq.mode` | **`pollable_quote`** (M1 decision; not fill-only degrade) |
| `BybitBookTick` / `BybitTradeTick` | `monitor.quotes` | De-multiplied L1 + trades from Bybit WS |
| `FluxionPoolStateTick` / `FluxionSwapTick` | `monitor.quotes` | Per-block AMM mid (wrapper + native) + swaps |
| `FluxionRfqQuoteTick` / `FluxionRfqFillTick` | `monitor.quotes` | Pollable RFQ quote + LOP settlement events |
| `CollectorConfig` | `monitor.collector` | `config/collector.yaml` tunables |

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
the next ticks. Local journal: `data/monitor.db` via `monitor.storage.SqliteStore`
(M2 / WHI-731). TUI (M5) may still keep an in-memory view derived from the same
ticks.

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
| US closed / weekend quiet | ~7 rows/s all pairs | `bybit_book` (orderbook.1) | ~150 MiB/day |
| US open (orderbook.1 hot) | 3–5× weekend (order-of-magnitude) | `bybit_book` | ~0.5–0.8 GiB/day |

Derivation (weekend): \(7 \times 86400 \approx 6.0 \times 10^5\) rows/day; ~250 B/row
payload+index overhead → ~150 MiB/day. At 0.5 GiB/day open + shared free 5.2 GiB
→ **disk full in ~1–2 weeks** without retention. Measure live with:

```bash
python -m monitor.retention --growth-only
```

#### Policy (config: `collector.yaml` → `retention`)

| Table | Default TTL | Rationale |
|-------|-------------|-----------|
| `bybit_book` raw | **2 days** | Bulk of bytes. TUI cold-start uses ≤`edge_history_max_samples` (2k) recent books; sparklines use ≤240 points. |
| `bybit_book_1m` | **14 days** | Before deleting raw books older than the raw TTL, last-in-minute L1 is upserted here (downsample). Compact series for forensics / future cold-start. |
| `bybit_trades` | **7 days** | ≥ TUI 24h volume window (`volume_window_ms`). |
| `fluxion_pool_state` | **7 days** | Edge rebuild + sparklines. |
| `fluxion_rfq_quotes` | **3 days** | Poll tape; RFQ notional is small vs book. |
| `fluxion_swaps` | **permanent** | M4 attribution feedstock (low volume). |
| `fluxion_rfq_fills` | **permanent** | M4 attribution feedstock (low volume). |
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
- 1m bars ≈ 10 pairs × 14 d × 1440 min × ~120 B ≈ **~25 MiB**
- trades + pool + RFQ quotes (TTL windows) + permanent swaps/fills ≪ book

→ **steady-state journal ≪ free disk** when TTLs hold.

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
5. **Schema:** `SCHEMA_VERSION=2` adds `bybit_book_1m` + prune indexes via
   `CREATE IF NOT EXISTS` (no destructive migration). Meta key is updated for
   operators; readers do not gate on the integer.

#### Disk waterline (`retention.disk`)

| Level | Free space | Behavior |
|-------|------------|----------|
| ok | ≥ `warn_free_bytes` (2 GiB) | Config TTLs |
| warn | < warn, ≥ critical | Multiply pruneable TTLs by `warn_ttl_factor` (0.25) |
| critical | < `critical_free_bytes` (1 GiB) | Multiply by `critical_ttl_factor` (0.05); pause **new** `bybit_book` inserts until a later run clears critical; force `VACUUM` attempt |

`fluxion_swaps` / `fluxion_rfq_fills` TTLs are **never accelerated** (only an
explicit non-null TTL in config would prune them).

### 5.2 Mantle block ingest latency (WHI-749)

**Metric** (matches collector meta and probe):

```
latency_ms = max(0, recv_ts_ms - block_ts * 1000)
```

`block_ts` is the on-chain block timestamp; `recv_ts_ms` is local wall clock
**after** all per-block RPC (getBlock + Multicall3 + logs + optional receipts).
The value therefore includes host clock skew vs chain time, tip visibility on
the RPC LB, and processing — not “RPC RTT alone.”

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
| **M6** | WHI-735 | Web panel **plan doc only** |

Dependency chain: M0 → M1 → M2 → (M3 ∥ M4) → M5 → M6.

## 7. Rejected Alternatives

| Option | Why rejected |
|--------|----------------|
| New repo for phase-2 | Zero-commit state made overlay ≡ template; Linear project already points at `report/`; avoid moving 3k LOC |
| Continue WMNT/USDT0 product | User rejected phase-1 POC product direction 2026-08-01 |
| Web-first UI | TUI faster for operator; Web deferred to plan-only M6 |
| Historical backfill for live panel | Product is forward-looking paper arb, not another backtest |
| Single blended AMM+RFQ price | Must show both; quote availability and spreads still differ by pair and session, but closed ≠ RFQ-off (WHI-753) |
| Auto-trade / bot execution | Explicit non-goal |

## 8. Known Risks & Open Questions

| Risk / question | Owner |
|-----------------|-------|
| xChange RFQ **public quote** API may not exist → degrade to last RFQ fill | **Resolved M1:** public EXACT_INPUT quote is pollable; see `docs/references/m1-rfq-feasibility.md` |
| Closed hours assumed RFQ-dark / AMM-only pricing | **Resolved WHI-753:** liquid pairs still quote two-sided RFQ on weekends and track Bybit mid; see `docs/references/m4-closed-session-rfq.md`. Open-vs-closed *fill* rates still open. |
| Bybit xStocks **multiplier** must be applied or edges are nonsense | **Resolved M1:** `instruments-info.xstockMultiplier` + `de_multiplied_price`; snapshots in `config/pairs.yaml` |
| Fluxion pool ABI / fork lineage unknown until M1 (phase-1 Agni topic0 trap) | **Resolved M1:** UniV3-lineage factory/quoter; liquid xStock pools fee=3000 USDC. M2 still re-verifies topic0 on live swaps |
| Bybit quote is **USDT** while Fluxion AMM/RFQ quote is **USDC** — basis not modeled in M1 | M3 (knob `usdt_usdc_basis_bps`, default 0); PnL v2 same default — measure before production accuracy claims |
| Live book depth quality vs phase-1 single snapshot approximation | M2/M3; PnL v2 VWAP needs depth (L1 degrade until wired) |
| PnL v2 optimal search is sample-best, not continuous global max | WHI-756; acceptable for panel buckets $10–$10k |
| Mantle block ingest P95 / head_lag (LB not-found) | **Resolved WHI-749:** default `head_lag_blocks: 1`; SLO in §5.2; note `docs/references/m2-block-ingest-latency.md` |
| Heuristic thresholds (80% / 20 trades) unvalidated on xStocks | M4 |
| TUI library choice (textual vs rich) | **Resolved M5:** Textual |
