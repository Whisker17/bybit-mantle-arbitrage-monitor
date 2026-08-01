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

## Files

| File | Loader | Purpose |
|------|--------|---------|
| `pairs.yaml` | `monitor.symbols.load_pairs_config` | Fixed Bybit ⇄ Fluxion xStock inventory + RFQ mode (M1 / WHI-730). Cross-validates `low_liquidity` vs threshold/AMM and `quote_token_address` vs `contracts`. |
| `collector.yaml` | `monitor.collector.load_collector_config` | Live collector tunables (Bybit WS, Mantle poll, RFQ notional, SQLite path) — M2 / WHI-731. |
| `metrics.yaml` | `monitor.metrics.load_metrics_config` | Paper-edge size ladder, Bybit taker / gas / USDT–USDC basis, session hours, breach size — M3 / WHI-732. |
| `attribution.yaml` | `monitor.attribution.load_attribution_config` | Taker-label thresholds (arb-bot convergence, price-keeper size, activity regime, Bybit lead-lag) — M4 / WHI-733. Rules: `docs/references/m4-attribution-labels.md`. |
| `tui.yaml` | `monitor.tui.load_tui_config` | Panel refresh interval, SQLite path, reference edge size, sort defaults, history windows — M5 / WHI-734. |

Optional per-deployment override: untracked `pairs.local.yaml` / `collector.local.yaml`
/ `metrics.local.yaml` / `attribution.local.yaml` / `tui.local.yaml` are reserved for
later if needed; v1 loads the checked-in YAML only.
