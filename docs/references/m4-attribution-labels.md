# M4 attribution label rules (WHI-733)

Spec of record for mechanism + behavior labels. Tunable thresholds live in
`config/attribution.yaml` and are loaded by `monitor.attribution.load_attribution_config`.
Do not hardcode the numbers below in call sites — they are defaults, not constants.

## Goal

Answer: **is an arb bot re-pegging Fluxion to Bybit, or is an MM (RFQ) setting the
price?** Secondary: among AMM takers, who looks like bot / keeper / retail.

## Mechanism layer (per fill)

| Mechanism | Source tick | Meaning |
|-----------|-------------|---------|
| `amm` | `FluxionSwapTick` | Active taker hit the V3 pool (pool fee + slip paid by taker). |
| `rfq` | `FluxionRfqFillTick` | Limit-order / Atomic RFQ settlement — **MM quote-driven**. |

Every decoded fill is labeled with exactly one mechanism. RFQ fills are
receipt-enriched (WHI-768) with `pair_id` / maker / taker / direction / amounts
when Transfer legs map to inventory tokens; unmapped stock tokens still yield
maker recovery but may leave `pair_id` null. Pair-scoped RFQ share only counts
fills that carry a `pair_id`; unscoped fills still contribute to **global** RFQ
vs AMM share.

### Session is not mechanism (WHI-753)

**Do not** map US equity closed / weekend → “AMM-only” at the mechanism layer.
Mechanism is **always** fill-sourced (LOP vs pool Swap), independent of
`SessionKind`. Closed-session RFQ *quotes* remain available for liquid
inventory pairs and track Bybit mid — evidence and product wording live in
`docs/references/m4-closed-session-rfq.md` (research note; this section is
the normative M4 rule).

- Session filters (`open` / `closed` / `all`) only **segment** aggregates; they
  do not rewrite `amm` ↔ `rfq`.
- A closed-session RFQ fill is still `rfq` (MM-driven), not reclassified as
  AMM drift.
- `activity_regime` on AMM takers (`all_hours` / `rth_only`) is orthogonal: it
  describes *when those addresses trade the pool*, not whether the RFQ book
  exists outside RTH.

## Behavior layer

Two paths share the `BehaviorLabel` enum:

1. **AMM takers** (`assign_behavior_label` / `label_takers`) — unit of analysis is
   the **taker address** = Swap `recipient` (pool beneficiary), same convention as
   phase-1 `mba/m6_attribution.py`. Priority:
   `arb_bot → price_keeper → retail → unknown`.
2. **Full address path** (WHI-768 `assign_address_label` /
   `label_addresses_from_journal`) — combines AMM swaps + enriched RFQ fills +
   native ERC-20 transfers. Priority:
   `market_maker → arb_bot → rebalancer → price_keeper → retail → unknown`.
   Config `address_overrides` (and sticky `address_labels.source=manual` rows)
   win over auto. Orthogonal flag `is_rebalancer` is set whenever CEX-touch
   transfers fire, even if the primary label is `market_maker` / `arb_bot`.

Rules and thresholds for `market_maker` / `rebalancer` live in
`docs/references/mm-attribution-analysis.md` and `config/attribution.yaml`
(`market_maker:`, `rebalancer:`, `cex_wallets`).

### Features computed per address (within a window × pair scope) — AMM path

| Feature | Definition |
|---------|------------|
| `is_contract` | `eth_getCode(addr) ∉ {0x, empty}` → contract; else EOA. Unknown if RPC skipped. |
| `role` | Optional `entrypoint` / `internal` from one sample `eth_getTransactionByHash` (`probe_roles`; phase-1 m6 heuristic). |
| `n_trades` | Count of AMM trades for that address in scope. |
| `trades_per_day` | Frequency: \((n-1) / span_days\) over first→last trade timestamps; null if \(n < 2\) or zero span. |
| `n_buy` / `n_sell` | Counts of `buy_native` / `sell_native`. |
| `median_notional_usd` | Median absolute USDC-leg notional. |
| `open_share` / `closed_share` | Fraction of trades in US RTH open vs closed (`monitor.metrics.session`). |
| `activity_regime` | `all_hours` if `closed_share ≥ min_closed_share_for_all_hours` and `n ≥ min_trades_for_regime`; `rth_only` if closed share below that with enough samples; else `unknown`. |
| `convergence_ratio` | Share of trades where direction **narrows** Fluxion–Bybit mid gap (see below). Denominator = trades with both mids + known direction (`n_convergence_scored`). |
| `bybit_align_ratio` | Share of trades whose direction matches the sign of Bybit mid Δ over `lookback_ms` when \|Δ\| ≥ `min_move_bps`. Populate `AmmTradeEvent.bybit_mid_prev` via `resolve_bybit_mid_prev(..., lookback_ms=config.bybit_correlation.lookback_ms)`. |

### Convergence (per trade)

Given pre-swap Fluxion mid \(F\) and contemporaneous Bybit mid \(B\) (both USDC per
native, Bybit de-multiplied):

- If \(F > B\): `sell_native` converges (pushes \(F\) down); `buy_native` diverges.
- If \(F < B\): `buy_native` converges; `sell_native` diverges.
- If \(F = B\): undefined (excluded from ratio denominator).

### Behavior labels (mutually exclusive, priority order)

**AMM-taker path** (`assign_behavior_label`) — applied after features; first match wins:

1. **`arb_bot`** — `n_convergence_scored ≥ arb_bot.min_scored_trades` **and**
   `convergence_ratio ≥ arb_bot.min_convergence_ratio` **and**
   (`bybit_align_ratio` is null **or** `≥ arb_bot.min_bybit_align_ratio`).
   Default gate: ≥80% convergence on ≥20 **scorable** trades (DESIGN §2.5).
   Raw trade count alone is not enough — missing mids must not pad the sample.
2. **`price_keeper`** — `n_trades ≥ price_keeper.min_trades` **and**
   both directions have share ≥ `min_direction_share` **and**
   `median_notional_usd ≤ max_median_notional_usd` **and**
   no trade exceeds `max_trade_notional_usd`.
3. **`retail`** — `n_trades ≥ retail.min_trades` and neither bot label fired.
4. **`unknown`** — everything else (thin sample).

**Full address path** (WHI-768) prepends / inserts:

0. **`market_maker`** — `n_rfq_maker ≥ market_maker.min_rfq_maker_fills` **or**
   cross-pair bidirectional AMM + mean-reversion gates (see analysis note).
2b. **`rebalancer`** — after `arb_bot`, when
    `cex_touch_transfers ≥ rebalancer.min_cex_touch_transfers` against
    `rebalancer.cex_wallets`.

`is_contract` and `activity_regime` are **reported alongside** the label; they do
not override the priority list (a contract with weak convergence is still `retail`
or `unknown`, not auto-`arb_bot`).

## Aggregates (M5 secondary-page model)

`monitor.attribution.build_pair_attribution` / `build_attribution_snapshot` emit:

- Mechanism share: AMM count, RFQ count, RFQ/(AMM+RFQ).
- Convergence share: among scoped AMM trades with a defined convergence flag.
- Label trade-share: fraction of AMM trades whose taker carries each behavior label.
- Top-N takers: address, contract flag, label, n, notional, ratios, regime.

Session segmentation reuses `SessionKind` from metrics (open / closed / all).

## Aggregates scope notes

- **Time period** = caller's event window × `session` filter (`open` / `closed` /
  `all`). `window_start_ms` / `window_end_ms` describe that window; multi-bucket
  calendars (hourly bars) are left to M5 if needed.
- **Pair RFQ share** requires `RfqFillEvent.pair_id` (populated by WHI-768 receipt
  enrichment when the stock token maps to inventory). Unscoped RFQ fills still
  count in `build_global_mechanism_share` only.

## Acceptance / QA procedure (top-10 spot check)

After a live sample window (≥1 NYSE session preferred):

1. Run collector → load SQLite / in-memory ticks.
2. Join each `FluxionSwapTick` to pre-swap pool mid + Bybit mid (and
   `resolve_bybit_mid_prev` for lookback) → `amm_trade_from_swap` /
   `swap_notional_usd`; stamp RFQ fills with `session` + optional `pair_id`.
3. Optionally `classify_addresses_from_config` + `probe_roles_from_config`
   (`role_samples_from_trades`) and pass flags into `build_attribution_snapshot`.
4. For each pair, print `top_takers` (default N=10): address, `is_contract`,
   `role`, `label`, `n_trades`, `trades_per_day`, `convergence_ratio`,
   `bybit_align_ratio`, `activity_regime`.
5. Spot-check labels against Mantlescan + Bybit chart: high-convergence contracts
   trading both sides of the peg should read `arb_bot`; tiny bidirectional
   flow should read `price_keeper`; thin samples `unknown`.
6. If labels look wrong, retune `config/attribution.yaml` (do not hardcode
   thresholds in the TUI). DESIGN §8 flags 80%/20 as unvalidated on xStocks.

M5 owns the long-running join loop; this package exposes pure builders so the
QA path is the same functions the panel will call.

Thresholds are **configurable** and **documented here** — not magic numbers in UI.
Output types are pure dataclasses with no TUI dependency so M5 can import them
directly.
