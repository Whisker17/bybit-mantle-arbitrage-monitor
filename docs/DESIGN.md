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
  Bybit xStocks taker **0.15%** (15 bps; MEASURED 2026-08-11 via
  `GET /v5/account/fee-rate`, WHI-1042; maker is 10 bps but unmodeled —
  always charge taker) + Fluxion pool fee + Mantle gas + bilateral slippage.
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
`config/metrics.yaml` as **fixed-size diagnostics**):

```
edge_bps = direction_aware_spread_bps
         - bybit_taker_bps (15)  # WHI-1042; config/markets/bybit-fluxion.yaml
         - fluxion_fee_bps
         - bybit_slip_bps(Q)
         - fluxion_slip_bps(Q)
         - gas_bps(Q)
         - signed_basis_bps      # USDC premium; + when paying USDC (WHI-960)
         - withdrawal_fee_bps(Q) # dir1 stable USD; dir2 tokens×listed mid (WHI-961)
```

Inventory is pre-positioned on both sides (same model as phase-1); carry is an
aggregate cost, not per-fill amortization, unless revised with evidence.

**Overview headline Net (product, ADR-0002 / WHI-965; wired WHI-966):** the
Web overview **Net** column is defined at PnL v2 **Q\*** (same optimal
notional and direction as Bucket PnL), **not** at the fixed M3 $1 000
reference. API enrichment (`PnlOptimalSummary.overview_net_wire`) sets
`net_edge_bps` / `net_edge_direction` / `net_size_usd` from the optimal
cash-flow bps when `pnl_v2.status == ok` (incl. quote_aged); non-ok blanks
Net rather than falling back to $1 000. The M3 formula above remains the
wear algebra at a chosen \(Q\) for detail ladders. **TUI is frozen** and
still shows $1 000 Net until explicitly updated or retired.
`EdgeStats` / `breach_size_usd` stay on the fixed $1 000 rung (secondary
diagnostic; see ADR-0002 EdgeStats epoch policy).

**PnL v2** (below) is the cash-flow form of the same paper arb. Implementers
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

- **Two-sided inventory paper arb** (same as §1.2 / §2.3): **transfer cost is
  modelled per direction** from a measured fee schedule (WHI-961) — dir1
  (stable return) charges `stable_withdrawal_fee_usd` (measured 0 for
  USDC/USDT Mantle); dir2 (xStock Bybit→Mantle) charges
  `asset_withdrawal_fee_tokens × listed Bybit mid` where listed mid =
  de-multiplied mid × `bybit.multiplier`. Pairs without a measured token fee
  annotate `withdrawal_fee_kind=unknown` (never a silent 0). **No wallet
  budget checker.** Thin book / AMM range exhaust → `fillable=false`.
- **Size variable \(Q\)** (AMM path) = single-trade USD notional at
  **de-multiplied** Bybit mid. Matched base \(q\) is identical on both legs
  **after** Bybit fee rules (§2.6.2); see research note §4.2–4.4.
- **Directions** (same tokens as M3 `edge.py`):
  `buy_fluxion_sell_bybit`, `buy_bybit_sell_fluxion`.
- **Fluxion venues:** AMM (full size continuum + optimal search) and RFQ
  (**poll-native rows only** — not the AMM bucket ladder; rate limit).
- **Negative PnL is first-class** (gas-dominated micro buckets); never clamp to 0.

#### 2.6.2 Cash-flow formulas

Bybit taker fee \(f_b = 15\,\mathrm{bps}\) (config; MEASURED 2026-08-11 via
authenticated `GET /v5/account/fee-rate` on all seven Fluxion-liquid pairs —
maker 10 / taker 15; WHI-1042). The prior "maker = taker = 20" claim (WHI-959)
is false after the 2026-08-07 tier move. Paper edge always charges **taker**
(crossing side); maker discount is unmodeled. Spot fees are charged in the
**received** asset (Bybit help center): buy → fee in base; sell → fee in quote.
VWAP levels: \(p = p^{\mathrm{raw}}/m\), \(s = s^{\mathrm{raw}}\cdot m\) (notional
invariant; matched base \(q\) shares that unit). Full algebra in research note
§4.2.

| Direction | Buy leg (trader pays) | Sell leg (trader receives) | PnL (USD) |
|-----------|----------------------|----------------------------|-----------|
| `buy_fluxion_sell_bybit` | Fluxion: USDC spent to acquire net base \(q\) (fee-inclusive AMM; or RFQ `amountIn`) | Bybit: sell \(q\) at bid VWAP; USDT received after fee | \(\mathrm{USDT_{recv}} - \mathrm{USDC_{spent}} - G - \mathrm{basis\_usd} - \mathrm{withdrawal\_fee\_usd}\) (stable schedule) |
| `buy_bybit_sell_fluxion` | Bybit: buy gross base so **net** base \(= q\) after fee; USDT spent | Fluxion: sell \(q\) for USDC received (fee-inclusive AMM; or RFQ `amountOut`) | \(\mathrm{USDC_{recv}} - \mathrm{USDT_{spent}} - G - \mathrm{basis\_usd} - \mathrm{withdrawal\_fee\_usd}\) (asset tokens × listed mid) |

- \(G =\) `gas_usd_per_swap` (default $0.01), charged **once** per Fluxion leg
  (AMM and RFQ; default charge gas on RFQ too — conservative).
- USDT/USDC cash legs use a **signed** basis (WHI-960). Config
  \(\beta = \texttt{usdt\_usdc\_basis\_bps}/10^4\) is the **USDC premium over
  USDT** (positive when USDC is richer). Wear is direction-aware via
  `edge.basis_wear_bps` / PnL `_basis_usd`:
  - `buy_fluxion_sell_bybit` **pays** USDC → \(\mathrm{basis\_usd} = +\beta Q\) (cost).
  - `buy_bybit_sell_fluxion` **receives** USDC → \(\mathrm{basis\_usd} = -\beta Q\) (credit).
  bybit-fluxion ships \(\beta = 7.5\,\mathrm{bps}\) (Bybit `USDCUSDT` ~1.0007–1.0008;
  sibling bot repo `mantle-stocks-arbitrage-bots` DESIGN §2.3 + that repo's
  `docs/references/m1-stable-rail-and-xstocks-latency.md` snapshot).
  binance-pancake stays at 0 (both legs USDT). Breakdown keeps the signed
  line so UI can render a credit as negative wear. The dir2 credit assumes
  USDC is valued 1:1 against USDT after receipt (converting back to USDT
  incurs its own USDCUSDT fee/spread — not modeled here). Live per-timestamp
  feed is out of scope here (tracked in `docs/DEFERRED_ISSUES.md`).
- AMM fee: fee-inclusive amounts in cash-flow; UI wear breakdown may split fee
  vs impact **without** double-subtracting in \(\mathrm{PnL}\).
- RFQ: no separate pool-fee line. Rows are keyed by **poll size** (USDC
  EXACT_INPUT for buys; native base EXACT_INPUT for sells — matches
  `config/collector.yaml` today). Do not invent an RFQ impact curve for
  off-poll sizes; do not force RFQ onto the AMM \(Q\) grid (research note §4.3.2).

#### 2.6.3 Size ladders: M3 vs PnL v2

| Path | Sizes | Owner |
|------|-------|-------|
| **Overview Net** (Web headline) | **Q\*** from PnL v2 optimal search | **ADR-0002** + **WHI-966** (wired) |
| M3 fixed ladder `edge_bps` (§2.3) | **$1 000 / $5 000 / $20 000** (`config/metrics.yaml`) | Detail / wear waterfall / EdgeStats at fixed rung |
| PnL v2 AMM buckets + search (this section) | **$10 / $50 / $100 / $500 / $1 000 / $10 000** + continuous Q\* | WHI-756 engine; Web/API WHI-766 |
| PnL v2 RFQ | Collector poll notionals only (not the AMM bucket list) | WHI-756 / WHI-766 |

**ADR-0002 / WHI-966:** overview Net and Bucket PnL share Q\* size semantics
so they do not disagree in sign for structural size reasons. The fixed M3
ladder is **secondary** (diagnostics), not the overview Net definition.
Frozen TUI may still show Net at $1 000. **EdgeStats epoch policy:** keep
cumulative breach / distribution series on `breach_size_usd` ($1 000);
label detail panels as secondary — do not silently retarget.

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
| Freshness (WHI-821) | **Two separate thresholds.** (1) `collector_stale_ms` (default 30s) on `/api/health` only — process liveness (`collector_alive` / UI **feed down**). (2) `quote_max_age_ms` — **annotate** quiet legs via `quote_aged` + per-leg ages; **never wipe** tables because event-driven bookTicker is quiet. Default in `config/api.yaml` (300s); optional **per-market override** on `config/markets/{id}.yaml` `quote_max_age_ms` (e.g. binance-pancake 600s for closed-session bStocks). Quiet (pair ages) ≠ offline (process health). True empty states remain `no_book` / `no_pool` only. Align / RFQ-age / pool-block lag still deferred (see `docs/DEFERRED_ISSUES.md`). |
| Align | Dual-leg snapshot skew ≤ `align_skew_ms` |
| Min profit | Config threshold for **highlight / breach only** — raw PnL always emitted. Live default `pnl_v2.min_profit_usd: 1.5` (bot floor; WHI-962). Shared base config: also gates binance-pancake green highlights (drift still fail-open there because no σ) |
| Sequential drift bar (WHI-962) | Real execution is sequential (~10 min transfer). Panel admits a green “profitable” highlight only when `net_bps ≥ k × σ_session` **and** min-profit floors. σ is stale inventory from WHI-915 (`m8-delay-decay.md` @10m, open/closed); `k` defaults to **1.5** from the executing bot (not a fitted WHI-915 open-session k — that span derived none). Wire: `sigma_transit_bps`, `drift_premium_bps`, per-direction `clears_drift`. Fail-open when σ missing (e.g. binance-pancake). Does **not** recompute σ live; does **not** model favourable drift; TUI frozen |
| Thin book | Partial depth fill ⇒ unfillable (no silent partial) |

**UI stale disambiguation (WHI-821):** three formerly-identical "stale" strings:

| Surface | Meaning | Label |
|---------|---------|-------|
| Overview / detail row badge (`row.stale`) | No CEX book tick in journal | **no book** |
| Underlying `price_type=stale` / type label | Equity reference print too old | **price stale** |
| PnL v2 `quote_aged` (or legacy `status=stale`) | Quote leg(s) older than `quote_max_age_ms` | **quote aged** (numbers still shown) |
| Health banner when `!collector_alive` | Collector process / journal feed dead | **feed down** |

**Collector liveness & downtime (WHI-825 / WHI-835):** process supervision is
per-market (`xstocks-collector@{market}.service` with `Restart=always`; local
`dev-web.sh` multi-market pid/log + restart wrapper). In-process watchdog uses
**any tick-table write** (not per-symbol quote age) — reconnect after N s
silence, non-zero exit after M s so the supervisor restarts. Meta
`collector_heartbeat_ms` separates process liveness from quiet books.
Restart books `[last write, first write]` as `collector_gaps.source=collector_down`;
cumulative EdgeStats exclude that interval. Health `feed_state`:
`ok | feed_down | feed_quiet | gap` + `recovery_hint` (platform-aware: no
systemd hints on macOS).

**Exit must complete (WHI-835):** graceful cancel alone is not enough —
`asyncio.to_thread` workers on non-daemon `ThreadPoolExecutor` threads can
outlive `SystemExit` (closed httpx client + multi-attempt backoff stretched a
zombie to hours). Collector owns the default executor and
`shutdown(wait=False, cancel_futures=True)` on stop; Rpc skips retries when
the client is closed; after `watchdog.shutdown_grace_s` (default 15s) a
daemon timer calls `os._exit(exit_code)`. The local wrapper also polls
`collector_heartbeat_ms` and `kill -9`s a stalled child so restart cannot
wait forever. Last subsystem error is stamped
`collector_last_feed_error{,_ms,_source}`.

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

#### 2.6.8 Capture rate (WHI-963)

Qualifying poll cycles are **not** opportunities. The executing bot is
single-flight with ~6–7 min occupancy per cycle, so capture is bounded by
window count × occupancy.

- Pure math: `monitor.analysis.edge_quant` (`detect_windows` +
  `capturable_profit_single_flight`).
- Live assembly: `monitor.metrics.capture` over `JournalReader` bulk loaders
  (same sample/window path as `scripts/xstocks_edge_quant.py`). Cost stack
  may differ: the offline WHI-909 re-run injects live USDCUSDT premium +
  rebalance amortization; the panel uses market constants (see DEFERRED).
- Config `capture:` in `config/metrics.yaml` — `trade_duration_ms` (390 000 =
  bot `cycle_duration_s` 390) and `reentry_cooldown_ms` (420 000 = bot
  `reentry_cooldown_s` 420), citing bot `docs/DESIGN.md` §2.5 / §2.8.
  Trailing `lookback_ms` default 24 h; `sample_ms` 30 s.
- API: nested `capture` on overview rows (compact) + full snapshot (series +
  sparkline) on pair detail. TTL cache `capture_cache_ttl_s` default **30**
  (longer than PnL — trailing window is heavy). Books, depth, **and pool
  state** load at `sample_ms` (not raw ~2 s Mantle ticks / 1 Hz depth) so
  cold recompute stays O(pairs × lookback/sample).
- Rates normalize by **observed sample coverage** (`max(ts)−min(ts)`), not the
  configured lookback alone — short journals do not understate Cap $/d.
- Parity (window math): same `sample_ms` / `trade_duration_ms` /
  `reentry_cooldown_ms` / `size_usd` / span as `scripts/xstocks_edge_quant.py`
  → T=0 `capturable_profit_per_day` / `windows_per_day` within rounding when
  the cost stack matches (live defaults use bot occupancy 390s/420s; the
  offline script's primary go/no-go uses 5s flight + 1-day re-entry — pass
  matching flags to compare). WHI-909 corrected-stack numbers intentionally
  diverge from panel Cap $/d (live basis + rebalance amortization).
- Web: overview **Cap $/d** column (subline windows/day); detail Capture rate
  panel with hourly windows sparkline.
- Out of scope: forecasting; per-address competition. VPS P95 re-measure after
  deploy (acceptance gate; not automated in CI).

### 2.7 Multi-market metrics & attribution (WHI-773 / M7-4)

**Invariant:** one metrics/attribution code path for every market. Venue
parameters are injected at assembly time (`monitor.markets.MarketContext`);
algorithms under `monitor.metrics` / `monitor.attribution` stay market-agnostic.

| Concern | Source | Notes |
|---------|--------|-------|
| CEX taker fee | market `costs.cex_taker_fee_bps` → `MetricsConfig.bybit_taker_fee_bps` | Field name is historical; value is the active CEX venue fee (Bybit xStocks **15** taker WHI-1042, Binance spot **10**). |
| Gas per AMM swap | market `costs.gas_usd_per_swap` | Mantle ~$0.01; BSC inventory default $0.05 (non-zero constant). |
| Quote basis wear | market `costs.quote_basis_bps` → `usdt_usdc_basis_bps` | Signed USDC premium (bps); bybit-fluxion **7.5**, binance-pancake **0** (same quote). Engine signs by direction (WHI-960). |
| Stable withdrawal fee | market `costs.stable_withdrawal_fee_usd` → `MetricsConfig.stable_withdrawal_fee_usd` | Dir1 capital-return fee (USD). Measured **0** for USDC/USDT Mantle (WHI-961). |
| Asset withdrawal fee | per-pair `asset_withdrawal_fee_tokens` (inventory) | Dir2: `tokens × listed mid` (`dm_mid × bybit.multiplier`). Measured HOODX/CRCLX/NVDAX; else `withdrawal_fee_kind=unknown` (never silent 0). |
| Pool fee | inventory per-pool `amm.fee` (UniV3 units) | Injected into `AmmPoolState.pool_fee` at tick lift — not a global YAML. |
| Quote token decimals | market `dex.quote_decimals` | Mantle USDC=6; BSC USDT=18. API/CLI pass this into `amm_pool_from_pair_tick`. Pure `amm_pool_from_tick` always requires explicit decimals. Pair-wrapper defaults (6/18) remain only for frozen TUI call sites that omit the arg (Bybit-only until M7-5). |
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

### 2.8 Live DEX pool TVL (WHI-782)

**Why:** volume answers “is anyone trading here?”; TVL answers “is there
capital to trade against?”. Inventory YAML `est_liquidity_usd` is a static
snapshot (explicitly not live). V3 `liquidity` (L) is **virtual liquidity in
the current tick range**, not dollar TVL — never display L as TVL.

**Definition (normative):**

```
TVL_usd = base_balance × mid_quote_per_base + quote_balance × 1
```

- `base_balance` / `quote_balance` = ERC-20 `balanceOf` of the pool contract
  (base = wrapper share on Fluxion ERC-4626 pools; native on Pancake).
- `mid_quote_per_base` = same-block AMM mid for that base token
  (`mid_usdc_per_wrapper` from pool state).
- Quote (USDC/USDT) priced at $1.

**Not depth:** V3 concentrated liquidity means total TVL ≠ size you can eat
without moving price. Tradeable depth is the PnL v2 bucket table (§2.6). UI
tooltips and column titles must keep that distinction.

**Collection:** throttled wall-clock poll (`tvl_poll_interval_s`, default 30s)
on both markets. Cadence is independent of the ongoing slot0 stride, but a
due TVL sample **forces one slot0/mid fetch that block** (needed for
valuation mid) and may write an extra `fluxion_pool_state` row off-stride.
Two `balanceOf` calls per pool via Multicall3 when due; journal table
`dex_pool_tvl` (schema v7).

**Low-liquidity dimming:** overview `low_liquidity` uses live TVL vs the
inventory threshold (`low_liquidity_threshold_usd`, still config) when a sample
exists; falls back to the inventory-time flag until the first poll. Pairs
without an AMM pool remain low-liquidity regardless of TVL.

**API / Web:** overview rows expose `tvl_usd` + `tvl_as_of_ms` (detail overview
mirrors the same). History remains queryable via `JournalReader.pool_tvl_series`
for a future chart — not embedded on the 2s detail poll. Web DEX column group
shows TVL (`$K`/`$M` via the same notional formatter as volume). Until the first
sample, the cell uses the project-wide empty glyph (`—`); tooltip states
“waiting for first sample”.

### 2.9 Web Top-N seats = DEX-tradeable (WHI-791 + WHI-796 + WHI-822)

The overview collapsed view is **not** “top N by sort key over the full
inventory”. A row earns a Top-N seat only when it is **DEX-tradeable** via
either path:

1. **AMM path:** quotable AMM mid (WHI-795 `quotable_amm_mid` → wire
   `amm_mid != null`) **and** `!low_liquidity` (live or inventory TVL ≥
   `low_liquidity_threshold_usd`, default **$50k**) **and** no
   `pricing_anomaly` reason (WHI-822 / WHI-964). Empty pools with residual
   slot0 mids and non-positive mids are excluded. Extreme `|AMM − CEX|`
   (default `max_abs_amm_spread_bps: 300` in `config/metrics.yaml`, aligned
   with the executing bot's tradability gate) keeps mid/spread visible for
   investigation but denies seats, paper edge, and PnL v2 optimal — prefer a
   false negative over a fake fillable claim
   (`docs/references/whi-822-spyb-pricing-anomaly.md`).
2. **RFQ path:** two-sided RFQ quote with **positive** prices (`rfq_buy` and
   `rfq_sell` both present and &gt; 0). Covers Fluxion inventory with
   `amm: null` (AMZNx / COINx / MCDx) where the inventory low-liq bit would
   otherwise permanently exclude them — RFQ *is* their DEX leg.

dex:none without RFQ (binance-pancake CEX-only) is always non-tradeable.
Reason for the $50k AMM floor: existing inventory convention (M1 / bStocks)
separating display dust from capital large enough for paper arb; reusing it
avoids a second threshold that would drift. **Fewer than N rows is correct**
when the chain only has a handful of live pools (bStocks 2026-08-03: ~8 pairs
above $50k TVL out of 55) — padding with no_pool / empty_pool / dust CEX-vol
leaders is worse than an honest short list. Footer copy is
`Top K of M by <sort> (T tradeable on DEX)`. “Show all” still lists every
filtered pair with structured status badges (`no pool` / `empty pool` /
`invalid mid` / `price anomaly` / `low liq` / `no quote`).

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
| Python ≥3.13 + uv | runtime target (dev venv + VPS); WHI-971 |
| httpx | RPC + REST (existing `mba.rpc` pattern) |
| polars / duckdb | local analytics if needed; TUI may stay in-memory |
| **Textual** (TUI) | M5 chose Textual over rich for interactive two-level nav (DataTable + detail screen); pure view models stay library-free |
| **FastAPI** (read-only API) | WHI-757: serves overview/detail/trades/health JSON over collector SQLite; reuses TUI builders + M3/M4. WHI-979: optional `static_dir` serves `web/out` same-origin on `127.0.0.1` (primary arb-bot-vps shape — ADR-0003) |
| **Next.js static export** | WHI-757: build on laptop/CI, rsync `web/out` to VPS; no Node runtime on the 1GB box |
| systemd (+ optional nginx) | Primary: `xstocks-api.service` alone (uvicorn, 1 worker, MemoryMax) serves panel + `/api` same-origin (WHI-979 / ADR-0003). Optional nginx reverse-proxy only on panel-only hosts that are not co-tenant with trading keys |
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
| `monitor/underlying` | Pyth Hermes (+ optional Yahoo) equity reference → `underlying_prices` | WHI-778 (landed) |
| `monitor/retention` | thin CLI over `storage.retention` (`python -m monitor.retention`) | WHI-751 |
| `monitor/metrics` | edge, wear, session stats | M3 (landed WHI-732) |
| `monitor/attribution` | mechanism + behavior labels (+ MM/rebalancer, WHI-768) | M4 (landed WHI-733); MM productization WHI-768 |
| `monitor/tui` | live panel (Textual overview + detail); **frozen** after Web lands | M5 (landed WHI-734) |
| `monitor/api` | read-only FastAPI over the same journal + builders; optional same-origin static | WHI-757 (skeleton); WHI-979 static_dir |
| `web/` | Next.js static export (panel UI) | WHI-757 skeleton; WHI-758 overview; WHI-759 pair detail |
| `deploy/` + `scripts/deploy-web.sh` | systemd unit, optional nginx site, one-command redeploy | WHI-757; arb-bot layout WHI-979 / ADR-0003 |

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
| `fluxion_swaps` | **permanent** | M4 attribution feedstock (low volume). Also DEX 24h volume feedstock (WHI-777). |
| `fluxion_rfq_fills` | **permanent** | M4 attribution feedstock (low volume). WHI-768 enriches maker/taker/pair/amounts from receipts. |
| `erc20_transfers` | **permanent** | WHI-768 native xStock Transfer stream (inventory / rebalance feedstock; low volume). |
| `address_labels` | **permanent** | WHI-768 address → label + evidence (auto + manual override). |
| `rebalance_events` | **permanent** | WHI-768 CEX-touch deposit/withdraw stream. |
| `cex_volume_24h` | **7 days** | WHI-777 CEX REST 24h quote volume snapshots (Bybit `turnover24h` / Binance `quoteVolume`, ~60s poll). UI uses latest row per pair. |
| `dex_pool_tvl` | **7 days** | WHI-782 live DEX pool TVL (`balanceOf` × AMM mid, throttled ~30s). Capital-size metric — **not** V3 virtual L and **not** tradeable depth (depth = PnL v2 buckets). UI uses latest; detail may series. |
| `collector_gaps` | **30 days** | Ops history. |
| `underlying_prices` | **7 days** | WHI-778 equity reference (Pyth Hermes / Yahoo gap-fill). Dedup on `(ticker, as_of_ms, source)`. |

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
   pre-v4 `fluxion_rfq_fills`); v5 = `underlying_prices` (WHI-778); v6 =
   `cex_volume_24h` REST volume snapshots (WHI-777). Meta key is updated for
   operators; readers degrade gracefully when optional tables are absent.

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
| **Web skeleton** | WHI-757 | FastAPI read-only API + Next.js static export + systemd deploy on VPS (nginx optional; WHI-979 same-origin primary) |
| **Web overview** | WHI-758 | Full overview table (TUI-parity columns, status/stale banner, sort/filter) |
| **Web pair detail** | WHI-759 | Pair detail: spread chart, trade stream, edge stats, attribution |
| **M7 multi-market (Binance ⇄ Pancake bStocks)** | WHI-770… | Second market beside Bybit⇄Fluxion. **M7-1…M7-4** landed (inventory, domain, collectors, metrics/attribution — DESIGN §2.7). **M7-5 Web/API bar** (WHI-774) landed: `/api/markets` + `/api/{market}/…`, Web `/m/{market}/` switcher, RFQ hide + accumulating empty state. **WHI-790** expands binance-pancake inventory from top-10 to all **55** Binance bStocks (21 factory-verified V3 USDT AMM + 34 dex:none). |

### WHI-790 — full bStocks inventory (binance-pancake)

`config/markets/binance-pancake.yaml` inventories **all 55** Binance spot bStocks
(not top-10). AMM membership is PCS **V3 factory `getPool` + on-chain `token0/1`**
(USDT only, 21 pools at 2026-08-02); other bases are **dex:none** (CEX legs only).
Enum script + snapshot: `scripts/enumerate_bstocks_pools.py`,
`docs/references/m7-bstocks-enum-snapshot.json`. Dynamic Top-N display is
WHI-791; DEX-tradeable seat eligibility is WHI-796.

**Capacity (research host 2026-08-02; rows marked measured vs derived):**

| Path | Number | Notes |
|------|--------|-------|
| Combined WS streams | **165** (55× book+depth+trade) | Path ~3.5 KB; exchange cap 1024 streams/conn |
| Depth message rate (derived) | ~**550/s** if every `depth20@100ms` fires | 10-pair baseline ~100/s; watch CPU on 1GB VPS soak |
| CEX 24h volume REST | **1** full `/api/v3/ticker/24hr` | ~1.9 MB body, ~2.6 s wall; filter 55 symbols client-side |
| BSC pool multicall | **21** AMM pools / stride | dex:none never enter `ChainPoller` |
| Disk (derived) | ~5.5× CEX book/trade rows vs top-10; ~2.1× pool_state | Feed WHI-751 / WHI-775 |
| Underlying Yahoo (post Hermes pin) | **~8** prefer_yahoo tickers | SKHY/SPCX + thin ETFs; 30 new names Hermes-batched |



Dependency chain: M0 → M1 → M2 → (M3 ∥ M4) → M5 → Web (WHI-757 → 758…).
M7 is parallel product expansion after Web PnL v2; does not block Web polish.

## 7. Rejected Alternatives

| Option | Why rejected |
|--------|----------------|
| New repo for phase-2 | Zero-commit state made overlay ≡ template; Linear project already points at `report/`; avoid moving 3k LOC |
| Continue WMNT/USDT0 product | User rejected phase-1 POC product direction 2026-08-01 |
| Web-first UI (v1) | TUI first for operator speed; Web deferred until after M5, then full VPS stack (WHI-757) instead of plan-only M6 |
| Vercel / hosted Web + data egress | Operator wants data on-box; static export + FastAPI co-located with collector SQLite on `127.0.0.1` (tunnel access; no public panel listener on arb-bot-vps — WHI-979 / ADR-0003) |
| Public nginx panel on arb-bot-vps | Box holds trading keys + live `arb-bot.service`; owner rejected public listener (WHI-979) |
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
| Bybit quote is **USDT** while Fluxion AMM/RFQ quote is **USDC** — basis not modeled in M1 | **Resolved WHI-960:** signed direction-aware wear; bybit-fluxion `quote_basis_bps: 7.5` (USDC premium); binance-pancake stays 0. Live per-timestamp feed still open. |
| Live book depth quality vs phase-1 single snapshot approximation | M2/M3; PnL v2 VWAP needs depth (L1 degrade until wired) |
| PnL v2 optimal search is sample-best, not continuous global max | WHI-756; acceptable for panel buckets $10–$10k |
| Mantle block ingest P95 / head_lag (LB not-found) | **Resolved WHI-749:** default `head_lag_blocks: 1`; SLO in §5.2; note `docs/references/m2-block-ingest-latency.md` |
| Heuristic thresholds (80% / 20 trades) unvalidated on xStocks | M4 |
| TUI library choice (textual vs rich) | **Resolved M5:** Textual |

