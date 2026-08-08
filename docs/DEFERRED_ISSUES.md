# Deferred issues registry

A living log of issues that were **surfaced during review but consciously not fixed**
in the PR that found them. This is not a bug tracker for open work — it is the record of
*known, accepted debt*: things we decided to defer, so a future change touching the same
area starts from knowledge instead of rediscovery.

## How to use this file

- **Add an entry** whenever a review turns up a real issue that a PR deliberately leaves
  unfixed (scope, risk, or priority). Record it here in the same PR that defers it.
- **Reference it** before working on the affected area — check whether the thing you are
  about to "discover" is already logged, and whether a listed fix is now in scope.
- **Close an entry** by moving it to *Resolved* (bottom) with the PR/commit that fixed
  it, rather than deleting — the history is useful.
- Keep entries short. Link the originating tracker issue / PR and the code symbol so the
  entry stays findable as the code moves.

Severity is the reviewer's judgement at defer time: **High** (correctness/safety, fix
soon — anything touching key handling, RPC credentials defaults to at least High), **Medium**
(operational/perf, fix when convenient), **Low** (nit/consistency).

## Entry format

```markdown
- **<one-line description of the defect>** (<Severity>, <issue-id>).
  `<file>::<symbol>` — what's wrong, why it was deferred, and what the fix would be.
```

---

## Open

- **Live per-timestamp USDT/USDC basis feed** (Medium, WHI-960 → later).
  Panel ships a signed constant (`quote_basis_bps: 7.5` on bybit-fluxion).
  Owner-measured Bybit daily klines show meaningful range (median daily 4 bps,
  p90 7, p99 50; year extremes 0.9911–1.0059). Live `USDCUSDT` mid per sample
  is the eventual right answer; out of scope for WHI-960. DESIGN §8 residual.

- **No abs bound on signed `usdt_usdc_basis_bps` / `quote_basis_bps`** (Low, WHI-960).
  `ge=0` was dropped so the field can be signed; a fat-fingered 750 or −7500
  now validates. Consider `|x| ≤ 100` (or similar) when a live feed lands.

- **Unfillable PnL rows still stamp signed basis in the cost breakdown** (Low, WHI-960).
  `_unfillable_result` keeps direction-aware `basis_usd` even when `pnl_usd=0`
  and `fillable=False`. Cosmetic phantom credit on dir2; UI already treats
  non-ok rows as non-tradable. Zeroing basis on unfillable would be cleaner.

- **Web cost waterfall paints signed credits as cost bars** (Low, WHI-960 → Web).
  After signed basis, dir2 `basis_bps`/`basis_usd` are negative. Both
  `web/components/pair/edge-panel.tsx` waterfalls size bars with `Math.abs`
  and always use `bg-warning/70`, so a credit looks like a cost bar; only the
  numeric label shows the sign. Pre-existing rendering; negative basis was
  unreachable under `ge=0`. Fix: signed fill color / direction, or a credit
  style class. Out of scope for the metrics engine change.

- **Package cycle forces lazy `depth_math` imports in tests** (Low, WHI-960
  review surface). Chain: `monitor.bybit.depth_math` → `symbols.__init__` →
  `bstocks_load` → `markets` → `attribution` → `metrics` → `bybit_slip` →
  `depth_math` (partial). Tests (`test_metrics_pnl_v2`, `test_metrics_pnl_snapshot`)
  import `depth_math` lazily after metrics. Pre-existing architecture; fix by
  making `depth_math` import `monitor.symbols.multipliers` without pulling
  package `__init__` side effects (or slimming `symbols.__init__`).

- **WHI-908: fill-validation script duplicates M0 sample builder** (Low, WHI-908).
  `scripts/xstocks_fill_validation.py::build_amm_samples_with_meta` is a near-
  copy of `scripts/xstocks_edge_quant.py::build_amm_samples`, adding only the
  parallel `SampleMeta` list (pool age / basis). Deferred: research ticket
  scope; extracting a shared builder that optionally yields meta is a separate
  refactor. Fix: move sample construction into `monitor.analysis` (or a shared
  script helper) and have both scripts call it.

- **WHI-908: research scripts reach private JournalReader + sibling script
  helpers** (Low, WHI-908). Extends the WHI-866 private-mapper pattern:
  `scripts/xstocks_fill_validation.py` imports `_row_to_swap` and, via
  `importlib`, `_eq._in_gap` / `_eq._as_of_idx` / `_eq.load_*` from
  `xstocks_edge_quant`. Justified for offline analysis; no public bulk-load
  API yet. Fix: promote typed bulk loaders on `JournalReader` (and pure join
  helpers into `monitor.analysis`) and switch both M8 scripts.

- **WHI-866: research script imports private JournalReader row mappers** (Low, WHI-866).
  `scripts/xstocks_edge_quant.py` reaches `_row_to_bybit_book` / depth / pool /
  RFQ helpers. First cross-package private use; justified for offline analysis
  but no public bulk-load API. Fix: promote typed bulk loaders on
  `JournalReader` (or a `monitor.storage.rows` module) and switch the script.

- **WHI-866: threshold_sweep profit not strictly monotone in theory** (Low, WHI-866).
  Filtering base windows by `peak_edge_bps` is monotone for series-level
  single-flight when windows are independent; portfolio reordering can still
  free a slot for a richer later window. Fit scans without early break so the
  knee remains defined. Fix: document or assert monotone only for series-level
  capturable profit.

- **WHI-790: no 30-minute dual-market collector soak in the PR** (Medium, WHI-790 → ops).
  Acceptance asked for a stable 30m run with full 55-pair inventory. Unit tests +
  capacity math (DESIGN §WHI-790) cover structure; live soak needs
  `xstocks-collector@binance-pancake` on the VPS after deploy, then check
  `bybit_book` / `pool_state` row growth and error log spam for dex:none pairs.
  Fix: ops soak checklist on first post-merge deploy.

- **WHI-790: PCS StableSwap registry not exhaustively walked** (Low, WHI-790).
  `scripts/enumerate_bstocks_pools.py` only probes StableSwap factory bytecode
  presence. V2+V3 factory enumeration is complete; no bStock StableSwap hit
  observed. Fix: walk StableSwap plain-pool registry if PCS documents a stable
  getPool equivalent and a listing appears.

- **WHI-790: residual prefer_yahoo underlyings still serial** (Low, WHI-790).
  After Hermes RTH pin for 30 liquid names, ~11 tickers remain Yahoo gap-fill
  (SKHY, SPCX, FLNC, KORU, LITE, MUU, MVLL, NOK, DRAM, INTW, SNXX).
  `UnderlyingPoller` still fetches them serially. Acceptable load now; if more
  Yahoo-only names land, add concurrency/throttle or raise poll intervals.

- **WHI-796: client re-derives DEX seat status from wire fields** (Low, WHI-796).
  `web/lib/sort.ts::isDexTradeable` / `dexNonTradeableReason` reassemble
  eligibility from `amm_mid` + `low_liquidity` + `amm_quote_reason` + RFQ
  quotes + `pnl_v2.status`, while the Python side already has
  `quotable_amm_mid` and inventory `has_amm`. A new backend status must be
  mirrored in the browser ladder. Fix: serve `dex_tradeable` + reason on
  `/api/{market}/pairs` (single seam) and let the web only apply Top-N.

- **WHI-796: `no_pool` badge can misfire on real dust pools without mid** (Low, WHI-796).
  When `low_liquidity && amm_mid == null` and both `amm_quote_reason` and
  `pnl_v2.status` are absent, the client labels `no_pool`. That is correct
  for dex:none but also hits a real sub-threshold pool during a mid gap.
  Needs a wire `has_amm` / inventory-pool bit to distinguish. Show-all still
  lists the row.

- **Optional-table name literals + sqlite_master probes duplicated** (Low, WHI-789).
  `monitor/storage/reader.py::JournalReader._table_names` (six call-site string
  guards) and a second `sqlite_master` shape in `monitor/storage/retention.py` —
  same existence check, no shared `_has_table` / `OPTIONAL_TABLES` constant.
  Deferred to keep the WHI-789 regression fix minimal (drop process-lifetime
  memo only). Fix: one `_has_table(conn, name)` helper + a single optional-table
  name constant consumed by reader guards and the late-create regression test.

- **UncoveredCoverageProbe per-source try/except is a data clump** (Low, WHI-787).
  `monitor/underlying/coverage_probe.py::UncoveredCoverageProbe.probe_once` —
  `(yahoo_body, yahoo_err)` and `(hermes_feeds, hermes_err)` pairs are gathered
  with identical try/except shape then threaded into `evaluate_uncovered_probe`.
  Deferred as low severity with zero live uncovered tickers after WHI-787.
  Fix: a small `SourceProbe(name, body, error)` (or shared helper) that collapses
  the two fetch paths.

- **Yahoo KR listing without FX.USD/KRW silently writes non-USD underlyings** (Medium, WHI-785 → when re-adding KR prefer_yahoo).
  `config/underlying.yaml` `fx_usd_krw_feed_id` is null after WHI-785 (SKHY is
  USD ADR). Re-adding a KR Yahoo ticker without restoring the FX feed id leaves
  `needs_fx` false and `parse_yahoo_chart` can emit `currency=KRW` into a
  premium path that assumes USD — silent ~1000× wrong bps. No live KR consumer
  today. Fix: model validator rejecting `prefer_yahoo` when Yahoo may quote
  non-USD without `fx_usd_krw_feed_id`, or require explicit `yahoo_quote_currency`.

- **Overview table COLS header vs hand-written body `<td>` order dual-write** (Low, WHI-780 → when table grows).
  `web/components/pairs-table.tsx` — thead derives groups/`colSpan` from the `COLS`
  list, but body cells are still a manual `<td>` sequence. Adding/reordering a leaf
  requires editing both without a compile-time link. Deferred: existing table style
  pre-dates WHI-780; full data-driven body cells is a larger refactor than this
  header/Dir issue. Fix: one column descriptor that renders both header and cell.

- **WHI-778 VPS 30-minute underlying soak not run in this PR** (Medium, WHI-778 → ops).
  Local Hermes/Yahoo smoke covers all public tickers; VPS soak needs both
  collectors up ≥30m then
  `SELECT ticker, COUNT(*), MAX(as_of_ms) FROM underlying_prices GROUP BY ticker`.
  SPCX is public Nasdaq via Yahoo (WHI-787) — include it in the soak. Checklist in
  `docs/references/underlying-price-source.md`.

- **WHI-778 session hours duplicated in underlying.yaml vs metrics.yaml** (Low, WHI-778).
  `config/underlying.yaml` `session:` mirrors `metrics.yaml` with a comment
  "must match". Code already types `SessionConfig` from metrics; config files
  stay independent so underlying can load without metrics. Fix: load session
  from metrics by default with optional override, or a single shared session
  fragment.

- **WHI-778 classify_price_type takes stale thresholds as loose params** (Low, WHI-778).
  `session` + three `stale_after_*_ms` travel together at call sites. Pass
  `UnderlyingConfig` (or a small `StalePolicy`) instead of four scalars.

- **WHI-778 corporate-action jump→stale not detected on the collection side** (Medium, WHI-778 → WHI-779).
  Spec asked for anomaly-day stale when multipliers / underlyings jump.
  Collection only does age-based stale; premium alignment + jump detection
  needs both legs and is deferred to the display issue (WHI-779).

- **WHI-772 VPS 30-minute soak + co-resident memory not measured in this PR** (Medium, WHI-772 → M7-6 / WHI-775).
  Collector wiring lands with unit tests; acceptance “30 分钟实跑 / 0 结构性 gap /
  内存实测（与现采集器共存）” needs deploy host + keyed BSC RPC. Report totals to
  M7-6 capacity issue. Latency meta fields (`block_ingest_latency_*`) are wired.

- **bStocks live `uiMultiplier` refresh loop not implemented** (Medium, WHI-772 → later).
  Inventory snapshots `ui_multiplier` (M7-1); journal multiplies that snapshot at
  write time. Corporate-action drift needs on-chain `uiMultiplier()` / event
  subscription (noted open in `docs/references/m7-bstocks-inventory.md`). Latent
  while nearly all top-10 pairs are 1.0.

- **Open vs closed RFQ coverage / lag not measured on continuous tape** (Medium, WHI-753 → WHI-760).
  `docs/references/m4-closed-session-rfq.md` is a weekend one-shot (3 rounds +
  slim JSON sample). Needs a running collector through ≥1 NYSE RTH day then a
  re-analysis of `fluxion_rfq_quotes` (+ Bybit) for open/closed contrast and
  multi-hour lag. Tracked as WHI-760.

- **Address clustering not in M4** (Low, WHI-733 → later if needed).
  DESIGN §2.5 / §4.2 mention phase-1 clustering / top-beneficiary breakout;
  M4 ships contract/EOA + entrypoint/internal role probe + behavior labels.
  Cluster graph would help router-collapse but is not required for M5 consumption.

- **RFQ LOP fill topic0 not fill-observed** (Medium, WHI-730 → M2 → M4).
  `docs/references/m1-rfq-feasibility.md` / `monitor.fluxion.abi.TOPIC0_ORDER_FILLED` —
  M2 collectors subscribe with signature-derived 1inch LOP v4 topics and persist
  `fluxion_rfq_fills`, but no live fill was observed at inventory or wiring time.
  Confirm topic0 + decode layout from a real fill before M4 attribution trusts labels.

- **RFQ fill enrichment (pair/maker/taker/amounts)** — **resolved WHI-768**.
  Live collector receipt-enriches `fluxion_rfq_fills`; one-shot backfill via
  `python -m monitor.collector.backfill_rfq`. Unmapped stock tokens outside
  inventory native/wrapper can still leave `pair_id` null — pair-scoped RFQ
  share remains conservative; global mechanism share still counts those fills.
  Residual: expand token registry / `asset()` wrapper resolve if UNKNOWN rate
  stays high on live tape.

- **Fluxion V2 factory not published for xStocks** (Low, WHI-730 → later if needed).
  `config/pairs.yaml` / `AmmPool.kind` — inventory is V3-only; DESIGN still says
  “V2/V3”. Revisit if Fluxion publishes a V2 factory used by xStock pairs.

- **L1BookTracker parallel to DepthBookTracker** (Low, WHI-755 → cleanup).
  Production WS uses `DepthBookTracker` only; `L1BookTracker` + `apply_l1_side`
  remain for orderbook.1 unit tests. Port those fixtures onto depth=1 payloads
  and delete the L1-only tracker when convenient.

- **WHI-755 30-minute VPS growth/memory AC not measured in CI** (Low, WHI-755 → deploy).
  Unit + 45s live orderbook.50 smoke confirmed wiring; DESIGN §5.1 has order-of-
  magnitude depth growth. After deploy, run collector ≥30m with MemoryMax=300M and
  `python -m monitor.retention --growth-only` to pin real `bybit_depth` rows/day.

- **M3 `compute_edge` still L1-only** (Medium, WHI-732 → panel).
  WHI-766 wires `JournalReader.latest_bybit_depth` into PnL v2
  (`pnl_snapshot` reconstructs stepwise levels from precomputed VWAP curves).
  M3 edge / TUI net column still use L1 `BybitBookTick` only — multi-level
  Bybit slip on the $1K/$5K/$20K ladder is a separate change.

- **PnL v2 align / RFQ-age guards incomplete** (Medium, WHI-766 → polish).
  WHI-821 stopped reusing `collector_stale_ms` as a pair-level wipe gate:
  ages are annotations (`quote_aged` + `*_quote_age_ms`) via
  `quote_max_age_ms`. Still missing: dual-leg `align_skew_ms`, RFQ-only age,
  and pool block lag (`pool_stale_blocks`) from DESIGN §2.6.5 /
  hummingbot-pnl §6.

- **RFQ poll notional ≠ edge ladder sizes** (Medium, WHI-732 → M5 / collector).
  `config/collector.yaml` polls ~100 USDC / 0.1 native while `metrics.yaml` ladder
  is $1K/$5K/$20K; RFQ edges reuse the polled price with zero size slip. Prefer
  ladder-matched RFQ polls or flag `EdgeResult` with the quoted notional.
  M5 (WHI-734) mitigates on the overview **Net** column by selecting AMM-only
  fillable edges at `reference_size_usd`; detail RFQ edge panels (TUI and Web
  WHI-759) still show the unslipped ladder extrapolation — do not treat those
  as size-accurate.

- **NYSE holiday table years 2025–2027 only** (Low, WHI-732 → annual).
  `monitor.metrics.session._CALENDAR_YEARS` — `session_kind` raises outside the
  table so 2028 does not silently misclassify holidays. Extend the frozensets
  (or move to YAML) before first use in 2028.

- **VPS memory / browser acceptance not measured in-repo** (Medium, WHI-757 → first deploy).
  Acceptance asks for collector+API+nginx RSS on whi715-vps and a browser check
  against a real journal. Skeleton ships measure commands in `deploy/README.md`
  but no agent has SSH to the box. Operator records numbers (and any MemoryMax
  retune) on first `./scripts/deploy-web.sh` dogfood — optionally a short note
  under `docs/references/`.

- **`/api/pairs/{id}/trades` rebuilds full detail** (Low, WHI-757 → load if needed).
  `monitor.api.routes.pairs.get_pair_trades` calls `build_pair_detail` so labels
  match the detail page. Under 2s poll this is the expensive path. Split a
  trades-only builder when measured latency or MemoryMax pressure requires it.

- **OpenAPI lacks response schemas** (Low, WHI-757 → later).
  Routes still return `dict[str, Any]` after `to_json_dict`; `/docs` lists paths
  but not field shapes. WHI-758/WHI-759 keep hand-written wire types in
  `web/lib/types.ts` (overview + full pair detail) — still not generated from
  OpenAPI. Add Pydantic response models (or generate TS) when a consumer needs
  compile-time parity; the pair-detail type surface is large enough that drift
  risk is real.

- **`build_spread_series` `_as_of` is linear, now hot at 1200 pts** (Low, WHI-759 → if API lag).
  Each book sample scans pools + RFQ buy/sell histories from the start
  (`monitor.tui.builder._as_of`). Fine at 240 points; with
  `spread_history_max_points: 1200` and 2s detail polls this is ~O(n×hist)
  per pair. Replace with `bisect_right` or a merge-walk cursor if
  `/api/pairs/{id}` p95 exceeds MemoryMax/latency budget on the VPS.

- **Full shadcn/ui CLI not installed** (Low, WHI-758 → polish if needed).
  Spec allowed "shadcn/ui + Tailwind"; WHI-758 ships Tailwind v4 + hand-rolled
  `components/ui/{badge,button,input}` in the shadcn style (no `components.json`,
  no Radix). Fine for a dense ops table; run `shadcn init` only if later pages
  need the full component catalog (risk: name collisions with these files).

- **`web` `npm run lint` is a no-op** (Low, WHI-757 → later).
  `package.json` script is `next lint` but no eslint config / eslint-config-next
  is installed. Typecheck still runs inside `next build`. Wire eslint or drop
  the script when the front-end lint surface is real.

- **API `uv sync` installs full phase-1 stack on VPS** (Low, WHI-757 → later).
  `pyproject.toml` still pulls polars/pyarrow/duckdb/matplotlib/textual for the
  archive + TUI. An `api` optional extra would slim the 1GB box; not required
  for skeleton correctness.

---

## Resolved

- **WHI-790: dust AMM pools can still rank under default net_edge sort** (Low, WHI-790 → WHI-796).
  Closed by WHI-796: Top-N seats require DEX-tradeable (`!low_liquidity` +
  quotable mid, or two-sided RFQ); dust/no-pool rows never pad the board under
  any sort key including default `net_edge`.

- **Mantle block ingest P95 / head_lag not re-measured** (Medium, WHI-743 → WHI-749).
  Measured with `monitor.collector.latency_probe`; default
  `mantle.head_lag_blocks: 1`; M2 SLO revised in DESIGN §5.2; note
  `docs/references/m2-block-ingest-latency.md`.

- **Wrapper→native share conversion not in inventory** (Medium, WHI-730 → M2 / WHI-731).
  `monitor.fluxion.pools.fetch_pool_states` — each block Multicall3 includes
  ERC-4626 `convertToAssets(1e wrapper_decimals)` and stores
  `mid_usdc_per_wrapper`, `mid_usdc_per_native`, `wrapper_assets_per_share` in
  `fluxion_pool_state`.
