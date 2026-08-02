# config/

Non-secret runtime parameters — thresholds, feature flags, tunables — live here as YAML,
loaded into a typed, validated model at startup.

## The convention

- **Secrets** go in `.env` (never committed; `.env.example` is the template). They are
  credentials — API keys, private keys, webhook URLs.
- **Parameters** go here, in YAML, checked in. They are decisions — every value should
  trace to `docs/DESIGN.md` §2 or be flagged as unvalidated.
- Loading is **typed and fail-fast**: define a pydantic model per config file, parse at
  startup, and include cross-field validation (e.g. `min_x < max_x`). A bad config must
  kill the process with a clear error before any real work starts.
- Per-deployment overrides use an untracked `<name>.local.yaml` copy (gitignored), so
  checking out a release tag never conflicts with live settings.

## Multi-market layout (M7-2 / WHI-771)

A **market** is `{ id, cex, dex, costs, inventory }` plus a dedicated SQLite journal
(`data/monitor-{market}.db` — see `docs/adr/0001-per-market-sqlite.md`).

| Path | Loader | Purpose |
|------|--------|---------|
| `markets/bybit-fluxion.yaml` | `monitor.markets.load_market_file` / `monitor.symbols.load_pairs_config` | Default market: Bybit ⇄ Fluxion xStocks inventory + RFQ mode + costs (M1 body under `inventory:`). |
| `markets/binance-pancake.yaml` | `monitor.markets.load_market_file` | Binance ⇄ Pancake bStocks top-10 (M7-1). Multiplier semantics **multiply**. Collector runtime: M7-3. |
| `collector.yaml` | `monitor.collector.load_collector_config(..., market_id=)` | **v2** shared `logging` / `retention` + `markets.{id}` venue blocks (RPC, poll, sqlite_path). |
| `metrics.yaml` | `monitor.metrics.load_metrics_config` | Size ladder, session hours, PnL v2 search knobs. Venue fee/gas **overridden** at assembly from the market file `costs:`. |
| `attribution.yaml` | `monitor.attribution.load_attribution_config` | Taker-label thresholds (M4). |
| `tui.yaml` | `monitor.tui.load_tui_config` | Panel refresh, default `market`, sqlite path, reference edge size (also used by API builders). |
| `api.yaml` | `monitor.api.load_api_config` | FastAPI bind, default `market`, sqlite path, stale windows, PnL cache TTL, CORS. |
| `underlying.yaml` | `monitor.underlying.load_underlying_config` | WHI-778 equity feed map (Pyth Hermes ids, Yahoo gap-fill, poll cadence, stale thresholds). Toggle via `collector.yaml` `underlying.enabled`. Session hours mirror `metrics.yaml` (must stay in sync). Validated in `docs/references/underlying-price-source.md`. After code deploys that touch the poller, **restart every market collector** — see `deploy/README.md` (WHI-788). |

CLI entrypoints take `--market` (default `bybit-fluxion`). Journal migration from the
pre-M7-2 single file:

```bash
mv data/monitor.db data/monitor-bybit-fluxion.db
```

Optional per-deployment override: untracked `*.local.yaml` copies are reserved for later
if needed; v1 loads the checked-in YAML only.
