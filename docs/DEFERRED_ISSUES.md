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
  (`build_global_mechanism_share`). M5 (WHI-734) deliberately omits unscoped RFQ
  fills from pair detail trade streams and shows pair RFQ mechanism share as
  empty until enrichment lands — do not invent pair_id in the TUI.

- **Fluxion V2 factory not published for xStocks** (Low, WHI-730 → later if needed).
  `config/pairs.yaml` / `AmmPool.kind` — inventory is V3-only; DESIGN still says
  “V2/V3”. Revisit if Fluxion publishes a V2 factory used by xStock pairs.

- **USDT/USDC basis left at 0 bps** (Medium, WHI-732 → measure when live).
  `config/metrics.yaml` `usdt_usdc_basis_bps` / DESIGN §8 — M3 exposes the wear
  knob but ships 0 (1:1). Populate from a measured Bybit USDT vs Fluxion USDC
  series before treating net edge as production-accurate.

- **Live Bybit depth not on the quote tick** (Medium, WHI-732 → M2/M5).
  `BybitBookTick` is L1-only; `compute_edge(..., bybit_depth=)` supports VWAP walk
  but `build_edge_snapshot` has no depth source. Ladder rungs therefore share L1
  half-spread slip (differ by gas only on RFQ). Wire orderbook depth when M2
  streams it or M5 polls REST snapshots.

- **RFQ poll notional ≠ edge ladder sizes** (Medium, WHI-732 → M5 / collector).
  `config/collector.yaml` polls ~100 USDC / 0.1 native while `metrics.yaml` ladder
  is $1K/$5K/$20K; RFQ edges reuse the polled price with zero size slip. Prefer
  ladder-matched RFQ polls or flag `EdgeResult` with the quoted notional.
  M5 (WHI-734) mitigates on the overview **Net** column by selecting AMM-only
  fillable edges at `reference_size_usd`; detail RFQ edge panels still show the
  unslipped ladder extrapolation — do not treat those as size-accurate.

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
  but not field shapes. WHI-758 keeps hand-written wire types in
  `web/lib/types.ts` (overview + pair stub share them) — still not generated
  from OpenAPI. Add Pydantic response models (or generate TS) when a consumer
  needs compile-time parity.

- **Full shadcn/ui CLI not installed** (Low, WHI-758 → polish if needed).
  Spec allowed "shadcn/ui + Tailwind"; WHI-758 ships Tailwind v4 + hand-rolled
  `components/ui/{badge,button,input}` in the shadcn style (no `components.json`,
  no Radix). Fine for a dense ops table; run `shadcn init` only if later pages
  need the full component catalog (risk: name collisions with these files).

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
