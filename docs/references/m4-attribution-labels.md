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

Every decoded fill is labeled with exactly one mechanism. RFQ fills currently lack
pair / direction / taker enrichment (see `docs/DEFERRED_ISSUES.md`); they still
contribute to **global** RFQ vs AMM share, but pair-scoped RFQ share only counts
fills that carry a `pair_id`.

## Behavior layer (AMM takers only)

Unit of analysis is the **taker address** = Swap `recipient` (pool beneficiary),
same convention as phase-1 `mba/m6_attribution.py`. RFQ legs are not behavior-labeled
until taker enrichment lands.

### Features computed per address (within a window × pair scope)

| Feature | Definition |
|---------|------------|
| `is_contract` | `eth_getCode(addr) ∉ {0x, empty}` → contract; else EOA. Unknown if RPC skipped. |
| `n_trades` | Count of AMM trades for that address in scope. |
| `n_buy` / `n_sell` | Counts of `buy_native` / `sell_native`. |
| `median_notional_usd` | Median absolute USDC-leg notional. |
| `open_share` / `closed_share` | Fraction of trades in US RTH open vs closed (`monitor.metrics.session`). |
| `activity_regime` | `all_hours` if `closed_share ≥ min_closed_share_for_all_hours` and `n ≥ min_trades_for_regime`; `rth_only` if closed share below that with enough samples; else `unknown`. |
| `convergence_ratio` | Share of trades where direction **narrows** Fluxion–Bybit mid gap (see below). Denominator = trades with both mids + known direction. |
| `bybit_align_ratio` | Share of trades whose direction matches the sign of Bybit mid Δ over `lookback_ms` when \|Δ\| ≥ `min_move_bps`. |

### Convergence (per trade)

Given pre-swap Fluxion mid \(F\) and contemporaneous Bybit mid \(B\) (both USDC per
native, Bybit de-multiplied):

- If \(F > B\): `sell_native` converges (pushes \(F\) down); `buy_native` diverges.
- If \(F < B\): `buy_native` converges; `sell_native` diverges.
- If \(F = B\): undefined (excluded from ratio denominator).

### Behavior labels (mutually exclusive, priority order)

Applied after features; first match wins:

1. **`arb_bot`** — `n_trades ≥ arb_bot.min_trades` **and**
   `convergence_ratio ≥ arb_bot.min_convergence_ratio` **and**
   (`bybit_align_ratio` is null **or** `≥ arb_bot.min_bybit_align_ratio`).
   Default gate: ≥80% convergence with ≥20 trades (DESIGN §2.5).
2. **`price_keeper`** — `n_trades ≥ price_keeper.min_trades` **and**
   both directions have share ≥ `min_direction_share` **and**
   `median_notional_usd ≤ max_median_notional_usd` **and**
   no trade exceeds `max_trade_notional_usd`.
3. **`retail`** — `n_trades ≥ retail.min_trades` and neither bot label fired.
4. **`unknown`** — everything else (thin sample).

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

## Acceptance notes

- Thresholds are **configurable** and **documented here** — not magic numbers in UI.
- Live validation: after a sample window, manually spot-check top-10 takers; retune
  YAML if labels look wrong. DESIGN §8 flags 80%/20 as unvalidated on xStocks.
- Output types are pure dataclasses with no TUI dependency so M5 can import them
  directly.
