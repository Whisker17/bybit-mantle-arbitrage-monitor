## Spec report — WHI-783

Diff read in full (`git diff origin/dev...HEAD`, 1 commit `e320dcc`). Column order in `COLS` matches the spec's target layout exactly (identity → cex → dex → edge → vol_gap → result → reference); `vol_gap` is pre-existing WHI-777 and simply absent from the spec's abbreviated sketch.

### (a) Missing or partial

Nothing material. Reqs 1–4, 6–8 are implemented: `cex_vs_und` (`premium_bps`) in the CEX group, `dex_vs_und` (`amm_premium_bps`) in the DEX group not gated on `rfq`, so *"no empty RFQ column when no RFQ"* holds; labels renamed to `vs CEX` / `RFQ vs CEX` with `amm_spread_bps` / `rfq_spread_bps` untouched (req 7/8); the `Premium` column is gone from the Underlying group, whose `UnderlyingCell` still carries the price_type badge + `as_of` title (req 4).

**Req 9, "tests green": unverified.** Bash was blocked by an unavailable classifier for the whole session, so `uv run pytest` / `npm test` never ran. Math checks out by inspection — `_book` mid 100.10 → +10 bps, AMM 99.5 → −50, RFQ mean 99.6 → −40, matching `(equity_eq/und − 1)×10⁴` — but treat the suite as unrun.

### (b) Scope creep

1. **`SpreadPoint.rfq_premium_bps` is dead surface.** Added to `monitor/tui/model.py`, populated in `build_spread_series`, threaded through `web/lib/types.ts`, `spread-chart.ts` (`rfqPremium`, `hasRfqPremium`), and asserted in both test suites — but never rendered. Spec req 3 asks only for *"RFQ vs equity in hover when RFQ market"*, which the overview `DexVsUndCell` already satisfies from the existing overview field `rfq_premium_bps`; req 5 names three chart series and RFQ-vs-Und is not one. The TS doc comment claims *"hover / optional chart series"* — neither exists. AGENTS.md advertises the field as landed.
2. **De-Bybit-ification not asked for:** chart series `"Bybit mid"` → `"CEX mid"`, toggle `overlay Bybit mid` → `overlay CEX mid`, empty message `"Bybit book"` → `"CEX book"`. Correct for binance-pancake and consistent with WHI-780, but outside *"UI labels/tooltips only"* for the AMM/RFQ renames.
3. **Two new detail summary `Field`s** (`CEX vs Und`, `DEX vs Und`) replacing one. Spec req 5 scopes the detail page to *"chart series by basis"*; reasonable, but unrequested.

### (c) Implemented but looks wrong

None found. The new `build_spread_series` premium math mirrors `_premium_for_pair` field-for-field — same `equity_equivalent_mid(..., ui_multiplier=...)` for CEX and AMM, same `mean_mid` with no multiplier for RFQ, same `premium_bps` — so req 6 (*"do not regress WHI-779 math"*) holds. `snap.amm_mid` is `amm.mid_usdc_per_native` and `snap.rfq_*_mid` is `rfq_price(...)`, identical to the overview path. uPlot has no hardcoded series indices, so inserting `ammPremium` at index 4 is safe; `setData` and the create-effect arrays both grew. API serialization is generic (`asdict` recursion), so the new fields reach JSON without a route change.

One nit inside (c)'s scope: `hasPremium` now ORs in `hasRfqPremium`, so the *vs Und* toggle can enable with nothing plottable. Unreachable in practice — books drive the loop, so `hasCexPremium` is set whenever an underlying print exists.
