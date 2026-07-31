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
  (Fluxion = V2/V3 AMM + xChange Atomic RFQ; RFQ dominates open hours, AMM
  dominates closed hours).
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
| Fluxion RFQ | xChange Atomic RFQ public API if available | **open risk:** if no public quote API, degrade to last RFQ fill (M1 decides) |

### 2.3 Paper edge

For size ladder \(Q\) (USD notionals; ladder TBD in M3, phase-1 used
1k–100k as a starting reference):

```
edge_bps = direction_aware_spread_bps
         - bybit_taker_bps (10)
         - fluxion_fee_bps
         - bybit_slip_bps(Q)
         - fluxion_slip_bps(Q)
         - gas_bps(Q)
```

Inventory is pre-positioned on both sides (same model as phase-1); carry is an
aggregate cost, not per-fill amortization, unless M3 revises with evidence.

### 2.4 Session segmentation

All time-weighted stats split by US regular equity hours (exchange calendar TBD
in M3; default America/New_York RTH 09:30–16:00).

### 2.5 Attribution

1. **Mechanism:** RFQ prints → classify as MM-driven; AMM swaps → active taker.
2. **Behavior:** address heuristics imported from phase-1 M6 (contract vs EOA,
   entrypoint vs internal, clustering). Convergence-ratio rule of thumb from
   product discussion: ≥80% with ≥20 trades → arb-bot candidate (validate in M4).

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
| textual or rich (TUI) | pick in M5; not locked yet |
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
| `monitor/bybit` | live Bybit book/trades WS | M2 |
| `monitor/fluxion` | AMM state/quotes + RFQ feed | M2 |
| `monitor/metrics` | edge, wear, session stats | M3 |
| `monitor/attribution` | mechanism + behavior labels | M4 |
| `monitor/tui` | live panel | M5 |

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

_(M2 fills `QuoteTick` / feed protocols. Strategy/metrics code should depend only on
quote interfaces, not on WS client internals.)_

### 4.4 Core flows

1. **Boot** → load symbol list + multiplier map → open Bybit WS + Fluxion poll/WS.
2. **On tick** → update mid/L1 → recompute paper edge per size → push TUI model.
3. **On chain event / RFQ print** → attribution classifier → session buckets.
4. **Session roll** → close open-bucket stats, open next.

### 4.5 State & recovery

In-memory from process start. Restart = empty history (by design: no backfill).
Optional local journal is out of scope for v1 unless M2 finds a hard need.

## 5. Data & Observability

- TUI is the primary observability surface in v1.
- Structured logs for feed disconnects / RPC errors (destination TBD in M2).
- No production PagerDuty-style alerting in v1.

## 6. Milestones

| ID | Linear | Success criterion |
|----|--------|-------------------|
| **M0** | WHI-736 | Three-commit history (phase1 tag → template → monitor skeleton); phase-1 pipeline still runs; DESIGN has reuse map |
| **M1** | WHI-730 | Symbol list + Fluxion/Bybit inventory + RFQ API feasibility + multiplier map |
| **M2** | WHI-731 | Live collectors for Bybit + Fluxion AMM (+ RFQ or degraded) |
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
| Single blended AMM+RFQ price | Must show both; regimes differ open vs closed |
| Auto-trade / bot execution | Explicit non-goal |

## 8. Known Risks & Open Questions

| Risk / question | Owner |
|-----------------|-------|
| xChange RFQ **public quote** API may not exist → degrade to last RFQ fill | **Resolved M1:** public EXACT_INPUT quote is pollable; see `docs/references/m1-rfq-feasibility.md` |
| Bybit xStocks **multiplier** must be applied or edges are nonsense | **Resolved M1:** `instruments-info.xstockMultiplier` + `de_multiplied_price`; snapshots in `config/pairs.yaml` |
| Fluxion pool ABI / fork lineage unknown until M1 (phase-1 Agni topic0 trap) | **Resolved M1:** UniV3-lineage factory/quoter; liquid xStock pools fee=3000 USDC. M2 still re-verifies topic0 on live swaps |
| Live book depth quality vs phase-1 single snapshot approximation | M2/M3 |
| Heuristic thresholds (80% / 20 trades) unvalidated on xStocks | M4 |
| TUI library choice (textual vs rich) | M5 |
