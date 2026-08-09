# AGENTS.md

This file provides guidance to coding agents (Claude Code, Codex, etc.) working in this
repository. `CLAUDE.md` is a symlink to this file — edit here only.

## What this is

Bybit spot ⇄ Fluxion (Mantle) tokenized-stocks (xStocks) live arbitrage panel. Phase-1 WMNT/USDT0 offline backtest is archived under src/mba and report/

The full PRD — requirements, architecture, milestones, rejected alternatives, open
risks — lives in `docs/DESIGN.md`. Read it before making any design or architectural
decision; do not re-derive parameters or decisions that are already validated there.

## Status

<!-- Keep this section current: what has landed, what is architected-for but NOT
implemented yet. Update it the moment reality changes instead of leaving stale
placeholders. Agents must not assume a module exists until its issue lands. -->

- **Phase 1 (archived, tag `phase1-backtest`):** offline WMNT/USDT0 backtest under
  `src/mba/` with delivered reports in `report/`. Pipeline still runs against local
  parquet under `data/`.
- **Phase 2 (active):** Bybit ⇄ Fluxion xStocks live panel. Product decisions are in
  `docs/DESIGN.md`.
  - **M1 (WHI-730) landed:** Bybit ⇄ Fluxion inventory + `monitor/symbols`
    (fixed overlap list, Bybit multiplier map, RFQ mode `pollable_quote`).
    Research notes under `docs/references/m1-*.md`. Inventory path moved to
    `config/markets/bybit-fluxion.yaml` in M7-2.
  - **M2 (WHI-731) landed:** live collectors — `monitor/bybit` (WS book+trades),
    `monitor/fluxion` (per-block pool state, swaps, RFQ poll + LOP fills),
    `monitor/storage` (SQLite), `monitor/collector` daemon
    (`python -m monitor.collector`). Tunables in `config/collector.yaml`
    (v2 per-market sections since M7-2).
    Block ingest SLO / `head_lag_blocks: 1` measured WHI-749
    (`docs/references/m2-block-ingest-latency.md`, DESIGN §5.2).
  - **M3 (WHI-732) landed:** `monitor/metrics` — spread bps (Bybit mid vs AMM /
    RFQ), net paper edge with wear breakdown at $1K/$5K/$20K, NYSE open/closed
    session segmentation, cumulative P50/P95/P99/max + cost-floor breach stats.
    Tunables in `config/metrics.yaml`.
  - **M4 (WHI-733) landed:** `monitor/attribution` — mechanism RFQ/AMM labels,
    AMM taker behavior pipeline (contract/EOA, convergence, activity regime,
    Bybit lead-lag → arb_bot / price_keeper / retail / unknown), pair aggregates
    for M5. Tunables in `config/attribution.yaml`; rules in
    `docs/references/m4-attribution-labels.md`.
  - **M5 (WHI-734) landed:** `monitor/tui` — Textual live panel (overview table
    + pair detail). Reads collector SQLite; spreads/edge via M3, attribution via
    M4. Tunables in `config/tui.yaml`. Entry: `python -m monitor.tui`.
  - **Retention (WHI-751) landed:** SQLite prune + `bybit_book` → 1m downsample,
    disk waterline (warn/critical), in-collector loop + `python -m monitor.retention`.
    Policy in `config/collector.yaml` `retention:`; design math in DESIGN §5.1.
  - **Closed-session RFQ research (WHI-753) landed:** weekend RFQ still
    two-sided on liquid pairs and tracks Bybit mid — session ≠ mechanism; note
    in `docs/references/m4-closed-session-rfq.md` + DESIGN / M4 rule updates.
  - **PnL v2 research (WHI-754) landed:** Hummingbot CEX⇄AMM methodology →
    cash-flow PnL spec; note `docs/references/hummingbot-pnl.md`, DESIGN §2.6.
  - **PnL v2 depth collector (WHI-755) landed:** Bybit `orderbook.50` →
    stateful N-level book; L1 still `bybit_book`; precomputed bucket VWAP curve
    in `bybit_depth` (throttled). Config `bybit.depth` in `config/collector.yaml`.
  - **PnL v2 engine (WHI-756) landed:** pure cash-flow PnL under
    `monitor/metrics/pnl_v2.py` — `compute_pnl_usd`, `pnl_bucket_table`
    ($10/$50/$100/$500/$1K/$10K), `optimal_size` (log grid + peak refine),
    RFQ poll-keyed rows, `OptimalPnlStats` session distributions. Config
    `pnl_v2:` in `config/metrics.yaml`. CLI: `python -m monitor.metrics`.
    Does not change M3 TUI ladder / `OverviewModel`.
  - **Web skeleton (WHI-757) landed:** `monitor/api` (FastAPI read-only over
    SQLite; reuses TUI builders), `web/` (Next.js static export), `deploy/` +
    `scripts/deploy-web.sh` (systemd + optional nginx). Tunables in
    `config/api.yaml`. TUI frozen for new features — Web is the surface for
    new metrics.
  - **Web overview (WHI-758) landed:** dark Tailwind overview table (TUI-parity
    columns + status bar + stale yellow banner + sort/filter + 2s poll + row
    → `/pair/{id}/`).
  - **Web pair detail (WHI-759) landed:** `/pair/{id}/` full detail — uPlot
    spread series (AMM+RFQ, open/closed bands, optional Bybit mid), Fluxion
    trade stream, edge stats + cost waterfall + session distributions,
    attribution (top takers + mechanism donut). SpreadPoint gains
    rfq_spread_bps + bybit_mid for the chart.
  - **Web/API PnL v2 (WHI-766) landed:** `monitor/metrics/pnl_snapshot.py`
    (journal ticks → dual-direction tables); `/api/pairs` row field `pnl_v2`
    (optimal summary); `/api/pairs/{id}` full `pnl_v2.tables` + costs;
    process-local TTL cache (`pnl_cache_ttl_s` in `config/api.yaml`, default
    2.5s). Overview Bucket PnL column + detail bucket panel replace placeholders.
  - **MM attribution research (WHI-767) landed:** chain-backfill analysis +
    draft `market_maker` / `rebalancer` rules — note
    `docs/references/mm-attribution-analysis.md`, pure helpers
    `monitor.attribution.mm_draft`, CLI
    `scripts/mm_attribution_analysis.py`.
  - **MM productization (WHI-768) landed:** RFQ fill receipt enrichment
    (maker/taker/pair/amounts) + native xStock `erc20_transfers` stream;
    `address_labels` + `rebalance_events` tables; full-address labels
    `market_maker` / `rebalancer` via `monitor.attribution.address_labels`
    + config thresholds / `cex_wallets` / manual overrides; CLIs
    `python -m monitor.collector.backfill_rfq` and
    `python -m monitor.attribution.refresh`. Schema v4.
  - **MM panel UI (WHI-769) landed:** API + Web full wiring — overview
    `mm_active` three-state badge; detail `address_panel` (labels +
    evidence hover); `GET /api/pairs/{id}/mm` inventory curves + rebalance
    timeline; pure builders in `monitor.attribution.mm_panel`. Empty
    states: accumulating / no_candidates / ok (no dashed placeholders).
  - **M7-1 inventory (WHI-770) landed:** `docs/references/m7-bstocks-inventory.md`
    — initial top-10 Binance ⇄ Pancake V3 bStocks (on-chain-verified pools),
    BEP-677 `uiMultiplier` pricing (multiply Binance mid for raw compare; ≠
    Bybit divide), AMM-only Terminal (no RFQ), US VPS geo: `api.binance.com`
    451 / use `data-api.binance.vision` + `data-stream.binance.vision`.
    **Superseded for set size by WHI-790** (full 55 + factory enum).
  - **M7-2 multi-market domain (WHI-771) landed:** explicit **market** assembly
    — `monitor/markets`, `config/markets/{id}.yaml` (bybit-fluxion +
    binance-pancake), `collector.yaml` v2 `markets:` sections, per-market
    SQLite `data/monitor-{market}.db` (ADR-0001), CLI `--market` (default
    `bybit-fluxion`), metrics costs parameterized from market file, systemd
    `xstocks-collector@.service` + `DEV_MARKET` in `scripts/dev-web.sh`.
  - **M7-3 collectors (WHI-772) landed:** `monitor/binance` (bookTicker +
    depth20@100ms VWAP + aggTrade, multiply `ui_multiplier` into comparable
    columns) + BSC Pancake V3 chain poll via parameterized `ChainPoller`
    (no RFQ; `pool_state_every_n_blocks`; PCS Swap topic0). Journal
    `data/monitor-binance-pancake.db` reuses existing table names (ADR-0001).
    Config `markets.binance-pancake` in `collector.yaml`; optional
    `BSC_RPC_URL`. Run: `python -m monitor.collector --market binance-pancake`.
  - **M7-4 metrics/attribution (WHI-773) landed:** same M3/M4/PnL v2 algorithms
    for `binance-pancake` — costs via `apply_market_costs`, mechanism RFQ off
    via `apply_market_attribution(has_rfq=dex.has_rfq)`, pool geometry with
    BSC USDT 18d, CLI `python -m monitor.metrics --market binance-pancake`.
    DESIGN §2.7.
  - **M7-5 Web/API multi-market (WHI-774) landed:** `GET /api/markets` +
    market-scoped `/api/{market}/pairs|health|…` (legacy unscoped routes map
    to default `bybit-fluxion`); Web `MarketSwitcher` + routes
    `/m/{market}/` and `/m/{market}/pair/{id}/` (root + legacy `/pair/{id}/`
    redirect to default market); RFQ columns hidden when `has_rfq` is false;
    explicit accumulating empty state when a market journal is not ready.
  - **Underlying price source (WHI-778 / WHI-787) landed:** research note
    `docs/references/underlying-price-source.md` (Pyth Hermes primary +
    Yahoo gap-fill for SKHY US ADR and Nasdaq SPCX; uncovered guardrail
    probes stale `uncovered` flags → WARN + `/api/health`); journal table
    `underlying_prices` (schema v5); `monitor/underlying` poller wired into
    both collectors; config `config/underlying.yaml` +
    `collector.yaml` `underlying.enabled`.
  - **Underlying premium panel (WHI-779) landed:**
    `monitor/metrics/premium.py` (de-multiplied mid / underlying − 1 → bps);
    API fields on pairs/detail (`underlying_*`, `premium_bps` /
    `cex|amm|rfq_premium_bps`, nested `premium` panel). Layout revised in
    WHI-783.
  - **CEX/DEX 24h volume (WHI-777) landed:** CEX REST poll (Bybit
    `turnover24h` / Binance `quoteVolume`, 60s) → journal `cex_volume_24h`
    (schema v6); DEX volume from collected swaps with truncation label when
    window &lt; 24h; overview columns CEX Vol / DEX Vol / CEX÷DEX ratio;
    detail `volume_compare` panel (session open/closed split); API fields
    `cex_volume_24h` / `dex_volume_24h` / `volume_ratio` on pairs + detail.
    Module `monitor/cex_volume` + `monitor/metrics/volume.py`. Both markets.
  - **Overview group headers + Dir labels (WHI-780) landed:** two-row
    overview thead (venue-named CEX/DEX groups + Edge/Vol gap/PnL/Underlying);
    column reorder so CEX Vol sits with CEX L1 and DEX Vol with DEX quotes;
    market-aware Dir short codes (`F→B`/`B→F` vs `P→B`/`B→P`) on Web + TUI.
  - **Pair badges TVL/Vol (WHI-781) landed (superseded by WHI-791):** had
    overview pair-id badges for hi-TVL / hi-Vol; removed once Volume/TVL
    became first-class Top-N sort columns.
  - **Premium column split (WHI-783) landed:** overview moves vs-underlying
    into CEX/DEX groups (`vs Und`); renames AMM/RFQ bps → `vs CEX` /
    `RFQ vs CEX`; Underlying group is reference price only; detail chart
    series by basis (DEX vs CEX, CEX vs Und, DEX vs Und). `SpreadPoint`
    gains `amm_premium_bps` / `rfq_premium_bps` (RFQ vs Und for API/hover;
    chart plots CEX + AMM only).
  - **DEX TVL column (WHI-782) landed:** live pool TVL via throttled
    `balanceOf` + AMM mid → journal `dex_pool_tvl` (schema v7);
    `tvl_poll_interval_s` on mantle/bsc; overview `tvl_usd` / `tvl_as_of_ms`
    + DEX-group TVL column; dynamic `low_liquidity` from live TVL (inventory
    flag is cold-start fallback). Capital size ≠ depth (PnL v2 buckets).
  - **bStocks full inventory (WHI-790) landed:** authoritative PCS V3/V2 factory
    enum (`scripts/enumerate_bstocks_pools.py` →
    `docs/references/m7-bstocks-enum-snapshot.json`);
    `config/markets/binance-pancake.yaml` expanded to **55** Binance bases with
    **21** collector-scope V3 USDT AMM pools (`pancake.amm`) and **34** dex:none
    (CEX-only; chain via `pairs_with_amm()`). QQQB restored as real high-TVL pool.
    Underlying Yahoo/uncovered map covers new tickers (`config/underlying.yaml`).
  - **Overview Top-N sort (WHI-791) landed:** Web overview defaults to Top 10
    by the active sort key (CEX Vol / DEX Vol / TVL / … are header-sortable);
    footer “Show all N / Collapse”; n/a sort values trail and never fill Top-N
    slots; shareable `?sort=&desc=&all=`; pair-id TVL/Vol badges +
    `web/lib/pair-badges.ts` removed.
  - **Top-N DEX-tradeable seats (WHI-796) landed:** collapsed Top-N only seats
    pairs with quotable AMM mid (WHI-795) **and** `!low_liquidity` (TVL ≥
    inventory `low_liquidity_threshold_usd`), **or** two-sided RFQ; no_pool /
    empty_pool / dust never pad the board; footer
    `Top K of M by <sort> (T tradeable on DEX)`; Show all keeps full list with
    status badges. Helpers in `web/lib/sort.ts` + label maps in `format.ts`.
  - **Empty-pool AMM quote gate (WHI-795) landed:** residual V3 `slot0` mid
    when `liquidity == 0` is no longer treated as a tradable AMM quote.
    Single seam `monitor.metrics.amm_quote.quotable_amm_mid` suppresses mid /
    vs CEX / vs Und / net edge with reason `empty_pool`; PnL v2 status
    `empty_pool` (distinct from `no_pool` / `stale`); bps magnitude guardrail
    `assert_sane_bps` (±5000) in tests.

  - **Underlying dual-market ops (WHI-788) landed:** bybit blank Underlying
    was a **stale collector process** (pre-778 binary; binance restarted later).
    Meta hardening: always write `underlying_last_poll_ms` / `underlying_last_n`
    (even when n=0) + `underlying_status` / `underlying_last_error`. Deploy
    notes: restart **both** `xstocks-collector@bybit-fluxion` and
    `@binance-pancake` after collector code ships (`deploy/README.md`;
    `deploy-web.sh` only restarts API).
  - **Pyth unpublished feeds (WHI-794) landed:** Hermes `price=0` /
    `publish_time=0` rejected at parse; Yahoo gap-fill for AAOI/AXTI/BE/EWY/
    NBIS/SOXL; UI/premium treat ≤0 or `as_of_ms==0` as n/a; reverse health
    `unpublished_pyth_feeds` + audit table in
    `docs/references/underlying-price-source.md`.
  - **PnL quiet-CEX stale fix (WHI-821) landed:** stop wiping bucket tables when
    event-driven CEX book age &gt; 30s; `collector_stale_ms` = process liveness
    only; `quote_max_age_ms` annotates `quote_aged` + per-leg ages; UI labels
    disambiguated (no book / price stale / quote aged / feed down). DESIGN §2.6.5.
  - **SPYB pricing-anomaly guard (WHI-822) landed:** investigation note
    `docs/references/whi-822-spyb-pricing-anomaly.md` — SPYB pool/token/decimals/
    uiMultiplier verified on-chain (not 张冠李戴); CEX tracks SPY, AMM is the
    deviant leg. Guard `max_abs_amm_spread_bps` (default 300, WHI-964 bot-aligned)
    → reason/status `pricing_anomaly` (mid/spread kept); blocks paper edge,
    PnL v2 optimal, and Top-N seats. Seam `annotate_pricing_anomaly` after
    `quotable_amm_mid`.
  - **Bucket PnL sort (WHI-824) landed:** overview Result-group column is a
    first-class sort key — `pnl_optimal_usd` (default, visible column unit) and
    `pnl_optimal_bps` (size-normalized). Header click cycles
    USD↓→USD↑→bps↓→bps↑; sort menu / `?sort=` jump either unit. Flat wire
    fields `pnl_optimal_net_usd` / `pnl_optimal_net_bps` when
    `pnl_v2.status == ok` (incl. quote_aged); non-ok nulls last and skip Top-N.
    Positive PnL highlighted, negative softened (`text-negative/60`); footer
    notes desc = least loss when all negative. TUI frozen (SortKey extended;
    builder leaves flat fields None).
  - **Collector supervision + watchdog + downtime gaps (WHI-825) landed:**
    multi-market `dev-web.sh` (per-market pid/log, restart wrapper, status
    red-flag); systemd template `Restart=always` + both markets enable docs;
    process-level write-activity watchdog (`config/collector.yaml` `watchdog:`)
    → WS reconnect then non-zero exit; restart records
    `collector_gaps.source=collector_down`; EdgeStats zero-weights that
    interval; `/api/health` + panel `feed_state` =
    `ok|feed_down|feed_quiet|gap` with `recovery_hint`.
  - **Watchdog exit unblock (WHI-835) landed:** hung `asyncio.to_thread`
    workers could pin the process after stop (closed-client retries for
    hours) so the restart wrapper never saw an exit. Fix: owned
    `ThreadPoolExecutor` + `shutdown(wait=False, cancel_futures=True)`,
    hard `os._exit` after `watchdog.shutdown_grace_s` (default 15s), Rpc
    fail-fast on closed clients, `dev-web.sh` external heartbeat stall →
    `kill -9`, platform-aware `recovery_hint` (no systemctl on macOS),
    meta `collector_last_feed_error*`.
  - **M8 xStocks edge quant / bot go-no-go (WHI-866) landed:** pure
    `monitor.analysis.edge_quant` (windows, single-flight capturable profit,
    knee threshold fit) + `scripts/xstocks_edge_quant.py` over the
    bybit-fluxion journal (PnL v2 engine, $500/$1k rungs, AMM vs RFQ).
    Report `docs/references/m8-xstocks-edge-quant.md` (+ companion JSON).
    Headline is AMM-only portfolio single-flight at ≤$1k/trade; gates the
    sibling `mantle-stocks-arbitrage-bots` M0. Study used `pricing_anomaly` gate **500**; **WHI-964** ships panel default **300**.
  - **M8 corrected cost-stack re-run (WHI-909) landed:** re-decide go/no-go
    under live USDCUSDT premium, 10/20 bps taker sensitivity (primary **20**),
    rebalance amortization 1/5/13.5 bps (skew-building dir only), extended
    threshold sweep 0..200. Pure helpers in `monitor.analysis.cost_stack` +
    edge_quant (kline HTTP stays in the driver script); prior WHI-866 report
    retained as `m8-xstocks-edge-quant-v1-whi866.md`. Schema v2 companion JSON.
  - **M8 on-chain fill validation (WHI-908) landed:** pure
    `monitor.analysis.fill_validation` + `scripts/xstocks_fill_validation.py`
    ranks top paper windows, matches journal `fluxion_swaps`, classifies
    taken / untaken-with-liquidity / untaken-too-thin, reports as-of join
    staleness and `pricing_anomaly` gate sensitivity (500→300 retains ~55.7% of that study's portfolio headline). Note
    `docs/references/m8-onchain-fill-validation.md` (+ companion JSON).
  - **M8 delay-decay / sequential cycle (WHI-915) landed:** pure
    `monitor.analysis.delay_decay` (realised PnL distributions, transit σ,
    `drift_premium_k`, clip-size optimum, sequential single-flight) +
    `scripts/xstocks_delay_decay.py`. Report
    `docs/references/m8-delay-decay.md` (+ companion JSON). Direction 1 only
    (`buy_fluxion_sell_bybit`); gates bot live trading / admission premium.
  - **Overview Net @ Q\* (WHI-965 ADR-0002 + WHI-966) landed:** Web/API
    overview **Net** uses PnL v2 optimal size Q\* (same bps / direction /
    notional as Bucket PnL) via `PnlOptimalSummary.overview_net_wire`;
    `net_size_usd` + Q\* chip under Net; non-ok blanks Net (no $1K fallback).
    EdgeStats / `breach_size_usd` stay on fixed $1K (secondary). TUI frozen
    at $1K Net. ADR `docs/adr/0002-overview-net-at-qstar.md`; DESIGN §2.3 /
    §2.6.3.
  - **Withdrawal fee cost line (WHI-961) landed:** direction-aware transfer
    cost in PnL v2 + M3 edge — dir1 `stable_withdrawal_fee_usd` (0 measured),
    dir2 `asset_withdrawal_fee_tokens × listed mid` (mid × multiplier);
    HOODX/CRCLX/NVDAX populated; unmeasured pairs annotate
    `withdrawal_fee_kind=unknown` (never silent 0). Web cost waterfall surfaces
    the line. DESIGN §2.6.1 amended.
  - **Sequential drift bar (WHI-962) landed:** per-pair session-split
    `sigma_transit_bps` (WHI-915 / m8-delay-decay @10m) on bybit-fluxion
    inventory; API `drift_premium_bps = k × σ` + per-direction
    `clears_drift` (`pnl_v2.drift_premium_k` default 1.5); overview Net /
    Bucket PnL mute + `drift` chip when net fails the bar; green only when
    min-profit floors **and** drift clear; `min_profit_usd: 1.5` (bot floor).
    Pure `monitor.metrics.drift`; TUI frozen.
  - **Capture rate panel (WHI-963) landed:** occupancy-bounded windows/day +
    capturable $/day via `monitor.metrics.capture` (reuses M8
    `edge_quant` single-flight math); `JournalReader` public bulk loaders
    (discharges WHI-866 deferred private-mapper debt for edge_quant);
    config `capture:` (`trade_duration_ms` 390s / `reentry_cooldown_ms` 420s
    bot-aligned); API `capture` on overview + detail with
    `capture_cache_ttl_s` 30; Web Cap $/d column + detail sparkline.
    TUI frozen.
  - **Type/lint gates restored (WHI-971) landed:** project targets CPython
    **3.13** (`requires-python` / ruff / mypy in lockstep); `uv run mypy`
    checks `src/monitor` only (strict, exits 0); `uv run ruff check .`
    excludes archived `src/mba`. Three None-leak sites fixed with
    regression tests. Unattended CI is **WHI-972** (follow-up; not in
    this issue).
  - **Web build gate (WHI-973) landed:** `next build` was failing on `dev` —
    `isCapturableOpportunity`'s `pnl_v2` param used `Pick<>` (fields required)
    while both call sites pass the wire shape (fields optional), so no static
    export could be produced. Both the row types and the predicate now name one
    shared `PnlOptimalFloorFields` (`web/lib/types.ts`) so they cannot drift
    apart again; `pnl.test.ts` keeps a hand-written copy on purpose as the
    outside oracle. Root cause was gate coverage: `tsx --test` strips types, so
    `npm test` could not catch it — `npm test` now runs `tsc --noEmit` first,
    and `npm run build` is documented as a **separate** gate (typecheck is a
    subset; it is not chained into `npm test`). Unattended CI is still
    **WHI-972**.
  - **Same-origin static panel (WHI-979) landed:** FastAPI serves `web/out`
    from `static_dir` (`/opt/xstocks/www` on VPS) when the directory exists —
    one tunnel to `127.0.0.1:8000` for panel + `/api/*`; discovery at
    `GET /__meta`. `fetchJson` names the resolved API base on network /
    non-JSON failures (dead tunnel / wrong `-L` port). arb-bot-vps: no
    nginx, no public listener; ADR-0003 + keepalive tunnel in
    `deploy/README.md`.
  - **US-host upstream gaps (WHI-974) landed:** bybit-fluxion on AS3635 —
    Fluxion RFQ intermittent 451s now leave `fluxion_rfq_quotes` rows (incl.
    intermediate failover failures); `/api/health` exposes `rfq_error_rate` +
    status counts; availability uses reachable (200/204) denominator only.
    Bybit REST 403 is a quiet `geo_blocked` meta transition (no 60s traceback
    spam); overview CEX Vol / ratio render `geo_blocked` (journal-derived
    volume stays detail-only). Note
    `docs/references/whi-974-us-host-upstream-gaps.md`.

## Build, test, run

```bash
uv sync                                       # install deps (creates .venv)
uv run pytest                                 # unit tests
uv run ruff check .                           # lint (live code; src/mba excluded, WHI-971)
uv run mypy                                   # type check (src/monitor only; WHI-971)
# Web gates — run BOTH before any PR touching web/ (WHI-973). tsx strips types
# without checking them, so tests alone cannot catch a bad signature.
# `npm test` runs `tsc --noEmit` first, but typecheck is a *subset* of the
# build: tsconfig includes `.next/types/**`, which only exists after a build,
# so `npm run build` is a separate gate and is NOT chained into `npm test`.
# Deps: `cd web && npm ci` once.
cd web && npm test                            # tsc --noEmit + pure-helper tests
cd web && npm run build                       # static export must compile → web/out
# Phase-1 pipeline (needs data/ parquet from a prior run):
uv run python -u -m mba.m5_report             # regenerate report/ from local parquet
# Phase-2 live collector (M2 / WHI-731); needs network + optional MANTLE_RPC_URL:
uv run python -m monitor.collector --market bybit-fluxion
# M7-3 Binance ⇄ Pancake collector; optional BSC_RPC_URL (vision WS hosts default):
uv run python -m monitor.collector --market binance-pancake
# Phase-2 TUI (M5 / WHI-734); reads collector SQLite (default data/monitor-bybit-fluxion.db):
uv run python -m monitor.tui --market bybit-fluxion
# Optional: uv run python -m monitor.tui --db /path/to/monitor-bybit-fluxion.db
# Journal retention (WHI-751); one-shot prune / growth report:
uv run python -m monitor.retention --market bybit-fluxion --growth-only
uv run python -m monitor.retention --market bybit-fluxion
# Block ingest latency probe (WHI-749); chain-only, no Bybit/RFQ:
uv run python -m monitor.collector.latency_probe --duration-s 600
# Phase-2 read-only Web API (WHI-757); needs collector journal:
uv run python -m monitor.api --market bybit-fluxion
# Optional: uv run python -m monitor.api --host 127.0.0.1 --port 8000
# PnL v2 cash-flow engine demo (WHI-756 / WHI-773); pure synthetic mids, no journal:
uv run python -m monitor.metrics
uv run python -m monitor.metrics --fluxion-mid 99.5 --json
uv run python -m monitor.metrics --market binance-pancake --pair-id TSLAB --amm-mid 99.5 --json
# Underlying equity smoke (WHI-778); Hermes public, no key:
uv run python -m monitor.underlying
uv run python -m monitor.underlying --tickers AAPL,TSLA,SKHY
# MM attribution research backfill (WHI-767); needs Mantle RPC + network:
#   uv run python scripts/mm_attribution_analysis.py --days 30
#   uv run python scripts/mm_attribution_analysis.py --skip-fetch
# M8 xStocks edge quant (WHI-866); needs bybit-fluxion journal:
#   uv run python scripts/xstocks_edge_quant.py --db data/monitor-bybit-fluxion.db
# M8 delay-decay sequential cycle (WHI-915); needs bybit-fluxion journal:
#   uv run python scripts/xstocks_delay_decay.py --db data/monitor-bybit-fluxion.db
# M8 xStocks edge quant / bot go-no-go (WHI-866); needs bybit-fluxion journal:
#   uv run python scripts/xstocks_edge_quant.py
#   uv run python scripts/xstocks_edge_quant.py --db data/monitor-bybit-fluxion.db --sample-ms 15000
# M8 on-chain fill validation of dislocation windows (WHI-908); needs journal:
#   uv run python scripts/xstocks_fill_validation.py
#   uv run python scripts/xstocks_fill_validation.py --db data/monitor-bybit-fluxion.db --sample-ms 20000
# WHI-768: enrich historical RFQ fills + refresh address labels from journal:
#   uv run python -m monitor.collector.backfill_rfq
#   uv run python -m monitor.attribution.refresh
# Web static export (build on laptop/CI — never on the 1GB VPS):
#   cd web && npm ci && npm run build   # → web/out
# Web pure-helper unit tests (format/sort):
#   cd web && npm test
# Deploy to VPS (rsync out/ + API sources, restart systemd):
#   ./scripts/deploy-web.sh user@host
```

## Runtime configuration

Secrets live in `.env` at the repo root (`.env.example` is the checked-in
template), loaded at startup — a missing required var must fail fast with a clear
error. **Never commit `.env`.** Non-secret runtime parameters (thresholds, feature
flags, tunables) live in `config/` as validated, typed config — not hardcoded, not in
`.env`. See `config/README.md` for the convention.

## Architecture

Module layout is fixed by `docs/DESIGN.md` §4.2. Short mirror:

- **`mba/`** — phase-1 offline WMNT/USDT0 backtest (archived, still runnable). Do not
  extend for xStocks.
- **`monitor/`** — phase-2 live multi-market panel (default Bybit ⇄ Fluxion). All new product code.
  - **`monitor/markets`** (M7-2) — market id, inventory path, costs, SQLite path assembly.
  - **`monitor/symbols`** (M1) — fixed pair list + Bybit multiplier helpers (default market inventory).
  - **`monitor/bybit`**, **`monitor/fluxion`**, **`monitor/storage`**,
    **`monitor/collector`** (M2) — live feeds → per-market SQLite.
  - **`monitor/binance`** (M7-3) — Binance public combined streams for
    binance-pancake; journal rows reuse `bybit_*` table names.
  - **`monitor/underlying`** (WHI-778) — Pyth Hermes (+ optional Yahoo)
    equity reference → `underlying_prices` (shared by ticker).
  - **`monitor/metrics`** (M3 + WHI-756 + WHI-766) — edge/wear (M3 ladder),
    session stats, PnL v2 cash-flow engine (`pnl_v2.py`) + journal snapshot
    assembly (`pnl_snapshot.py`).
  - **`monitor/attribution`** (M4) — mechanism + behavior labels / aggregates.
  - **`monitor/tui`** (M5) — Textual overview + detail panel over SQLite
    (**frozen** for new features).
  - **`monitor/api`** (WHI-757 + WHI-766) — FastAPI read-only JSON over the
    journal; PnL v2 fields on overview/detail with TTL cache.
  - **`web/`** (WHI-757+) — Next.js static export; overview (WHI-758);
    pair detail (WHI-759); PnL v2 column + bucket panel (WHI-766); deploy via
    `scripts/deploy-web.sh` + `deploy/`.
  Reuse pieces from `mba` per DESIGN §4.2 table; do not import whole stages.

## Git workflow (mandatory)

**One issue = one git worktree off latest `origin/dev` = one PR into `dev`.**
Do **not** implement issues in the primary clone working tree.

1. `git fetch` + create worktree/branch from `origin/dev`
   (`fix/whi-NNN-topic` or `feat/whi-NNN-topic`).
   **Check the issue's labels first** — an issue labelled `hotfix` branches off
   `origin/main` instead and targets `main` (see **Promotion lanes** below). Verify the
   base right after creating the worktree — `git merge-base HEAD origin/dev` must equal
   `git rev-parse origin/dev` — whatever tooling created it. *(Runtime aside: Claude
   Code's `EnterWorktree` defaults to `origin/main`, wrong for this lane. Any wrapper may
   have its own default; the check above is what settles it.)*
2. Implement only that issue; tracker state → **`In Progress`**.
3. `gh pr create --base dev` (title/body include `WHI-NNN`); tracker →
   **`In Review`**. Any review finding you intentionally leave unfixed goes in
   `docs/DEFERRED_ISSUES.md` as part of this PR — see that file for the format.
4. A PR whose implementation went through `/implement`'s full three-round review loop
   (plus the escalation pass, when round 3 left findings open) is **pre-authorized to
   self-squash-merge** once it reads MERGEABLE/CLEAN and tests + lint pass — no separate
   human approval. **Exceptions that stop at `In Review` for a human:** changes touching
   **key handling, RPC credentials**, and `release/*` → `main` promotions. PRs that skipped the
   review loop also stop at `In Review`. After merging, run the **post-merge cleanup**
   below.

### Post-merge cleanup (mandatory, in order)

Drive these from the **primary clone**; never commit to `dev` directly.

0. **If the PR is CONFLICTING** (`dev` advanced since you branched): inside the feature
   worktree, `git merge origin/dev`, resolve, rerun the affected tests, and `git push`.
   The PR must read **MERGEABLE / CLEAN** before you merge.
1. **Squash-merge + drop the remote branch:** `gh pr merge <N> --squash --delete-branch`.
2. **Remove the worktree:** `git worktree remove <worktree-path>` then
   `git worktree prune`.
3. **Delete the local branch:** `git branch -D fix/whi-NNN-topic`
   (this fails while the worktree still holds the branch — do step 2 first).
4. **Fast-forward local `dev`:** `git fetch origin --prune` then
   `git merge --ff-only origin/dev` (must fast-forward — do not create commits on
   `dev`).
5. **Tracker → `Done`.**

### Promotion lanes (`→ main`)

`main` **equals production** — always the last deployed tag. Never open a PR with `dev` as
head into `main` (the branch would be auto-deleted by `delete_branch_on_merge`). Two lanes
reach `main`, and picking the wrong one ships unreviewed work:

- **Release** — everything on `dev` is shippable. Cut a temporary `release/vX.Y.Z` from
  `dev`, PR → `main`. **Always a human gate.**
- **Hotfix** — production is broken *and* `dev` holds work that must not ship. Branch off
  `origin/main`, PR → `main`, then **merge `main` back into `dev`** or the next release
  re-ships the bug.

The decision rule: run `git log --oneline origin/main..origin/dev`. **If that list holds a
single commit you would not ship right now, you must use the hotfix lane.**

Merge strategy is per-lane: **squash** into `dev`, but **merge commit** into `main` —
squashing a release/hotfix disconnects the tag from `dev`'s history and silently breaks
`git log <tag>..origin/dev`. Bump the project version before tagging, **deploy from the
tag and never from a branch**, and keep the tracker Release ↔ git tag ↔ GitHub Release
triple in agreement (backfill the Release's `commitSha`).

Enable the local push guard once per clone **and per worktree**:
`git config core.hooksPath .githooks`.

Full rules: `docs/GIT_WORKFLOW.md`.

## Template feedback loop

This repo was bootstrapped from the shared project template
(`https://github.com/Whisker17/code-template`). When work here surfaces an improvement that belongs to the
**template layer** — a workflow rule that bit us, a skills configuration fix, a doc
convention worth standardizing — tell the user explicitly so they can port it back to
the template repo (and its `CHANGELOG.md`). Project-specific learnings stay here;
process-level learnings flow back.

## Agent runtime (any agent, any vendor)

This repo is runtime-neutral: Claude Code, Codex, or anything else. Nothing in the
workflow names a model. Instead, skills name a **role** — `REVIEWER`, `ESCALATOR`,
`EXPLORER` — mapped to real commands in `config/agent-roles.conf` and dispatched through
`scripts/agent-dispatch.sh`. Full contract: **`docs/agents/runtime.md`**.

Two rules matter more than the mechanism:

- **Review happens in a different context than implementation**, with a model at least as
  capable (cross-vendor preferred). Check the path before relying on it:
  `scripts/agent-dispatch.sh --probe`.
- **If the reviewer is unavailable, the review loop did not run** — finish the work, open
  the PR, and stop at `In Review` for a human. Self-review in the implementing context
  never authorizes a self-merge.

When a model generation turns over, edit `config/agent-roles.conf` and nothing else.

## Agent skills

Skills live in `.claude/skills/<name>/SKILL.md`. Runtimes that auto-discover them expose
each as `/<name>`; **in a runtime with no skill loader, read the file directly** — a skill
is just markdown. The load-bearing ones:

| Skill | Path |
|-------|------|
| `/implement` | `.claude/skills/implement/SKILL.md` |
| `/code-review` | `.claude/skills/code-review/SKILL.md` |
| `/grill-me` → `/to-spec` → `/to-tickets` | `.claude/skills/{grill-me,to-spec,to-tickets}/SKILL.md` |
| `/tdd`, `/diagnosing-bugs`, `/handoff`, `/triage` | `.claude/skills/<name>/SKILL.md` |
| `/ask-matt` (which skill do I want?) | `.claude/skills/ask-matt/SKILL.md` |

### Issue tracker

Issues and PRDs live in **Linear** (project `Mantle <> Bybit Arbitrage Monitor`, team
`Whisker-Personal`). Access is a fallback ladder — MCP tools, else the GraphQL API with
`LINEAR_API_KEY` — and reaching the tracker is mandatory, not optional: workflow state
moves in lockstep with the PR. External PRs are not a triage surface. See
`docs/agents/issue-tracker.md`.

### Triage labels

Canonical role names (`needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, `wontfix`) used verbatim as Linear labels. See
`docs/agents/triage-labels.md`.

### Domain docs

This repo's spec of record is `docs/DESIGN.md` (PRD: requirements, architecture,
milestones, rejected alternatives, open risks) plus `docs/adr/` for narrower decisions
made after v1 ships. See `docs/agents/domain.md`.
