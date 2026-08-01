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

- **RFQ fill rows lack pair/direction/size/price/taker** (Medium, WHI-731 → M4 / WHI-733 → M5).
  `fluxion_rfq_fills` stores order_hash + remaining + tx_hash only — `OrderFilled`
  does not encode pair identity. Enrich from receipt Transfer/LOP order bytes when
  a live fill is available. M4 ships pair-scoped RFQ share only for fills that
  already carry `pair_id`; unscoped fills still count in global mechanism share
  (`build_global_mechanism_share`). M5 (WHI-734) and Web pair detail (WHI-759)
  deliberately omit unscoped RFQ fills from pair detail trade streams and show
  pair RFQ mechanism share as empty until enrichment lands — do not invent
  pair_id in the TUI or Web.

- **Fluxion V2 factory not published for xStocks** (Low, WHI-730 → later if needed).
  `config/pairs.yaml` / `AmmPool.kind` — inventory is V3-only; DESIGN still says
  “V2/V3”. Revisit if Fluxion publishes a V2 factory used by xStock pairs.

- **USDT/USDC basis left at 0 bps** (Medium, WHI-732 → measure when live).
  `config/metrics.yaml` `usdt_usdc_basis_bps` / DESIGN §8 — M3 exposes the wear
  knob but ships 0 (1:1). Populate from a measured Bybit USDT vs Fluxion USDC
  series before treating net edge as production-accurate.

- **L1BookTracker parallel to DepthBookTracker** (Low, WHI-755 → cleanup).
  Production WS uses `DepthBookTracker` only; `L1BookTracker` + `apply_l1_side`
  remain for orderbook.1 unit tests. Port those fixtures onto depth=1 payloads
  and delete the L1-only tracker when convenient.

- **WHI-755 30-minute VPS growth/memory AC not measured in CI** (Low, WHI-755 → deploy).
  Unit + 45s live orderbook.50 smoke confirmed wiring; DESIGN §5.1 has order-of-
  magnitude depth growth. After deploy, run collector ≥30m with MemoryMax=300M and
  `python -m monitor.retention --growth-only` to pin real `bybit_depth` rows/day.

- **Live Bybit depth not wired into M3 edge / Web panel** (Medium, WHI-732 → panel / API).
  Collector journals precomputed bucket VWAPs in `bybit_depth` (WHI-755). PnL v2
  engine (WHI-756) accepts multi-level `bybit_bids`/`bybit_asks` with L1 fallback,
  but the TUI/Web still build edges from L1 `BybitBookTick` only — no live consumer
  loads `JournalReader.latest_bybit_depth` into `compute_edge` or `pnl_bucket_table`.
  Wire API/Web (and optionally M3 edge) when overview bucket-PnL column lands.

- **PnL v2 freshness / align guards at consumer** (Medium, WHI-756 → panel / API).
  DESIGN §2.6.5 and hummingbot-pnl §6 list `bybit_stale_ms` / `pool_stale_blocks` /
  `rfq_stale_ms` / `align_skew_ms`. The pure engine takes a coherent snapshot (same
  contract as M3 `compute_edge` — no timestamps) and does not emit `stale_*` / `skew`.
  Callers that load from SQLite must apply age/skew before accepting a `PnlResult` as
  live. Implement when Web/API wires `pnl_bucket_table`.

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

- **Mantle block ingest P95 / head_lag not re-measured** (Medium, WHI-743 → WHI-749).
  Measured with `monitor.collector.latency_probe`; default
  `mantle.head_lag_blocks: 1`; M2 SLO revised in DESIGN §5.2; note
  `docs/references/m2-block-ingest-latency.md`.

- **Wrapper→native share conversion not in inventory** (Medium, WHI-730 → M2 / WHI-731).
  `monitor.fluxion.pools.fetch_pool_states` — each block Multicall3 includes
  ERC-4626 `convertToAssets(1e wrapper_decimals)` and stores
  `mid_usdc_per_wrapper`, `mid_usdc_per_native`, `wrapper_assets_per_share` in
  `fluxion_pool_state`.
