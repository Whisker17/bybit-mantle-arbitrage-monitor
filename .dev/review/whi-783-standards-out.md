## Standards report — WHI-783 (`e320dcc`)

### Documented standards

**No hard violations found.**

- `src/monitor/tui/{builder,model}.py` — AGENTS.md ("Web overview (WHI-757) landed … **TUI frozen for new features**") could read as barring this. It doesn't: `monitor/api` "reuses TUI builders" per the same file, `build_spread_series`/`SpreadPoint` are the shared API path (`serialize.to_jsonable` auto-serializes the dataclass, so no API edit was needed), and no TUI *widget* gained a feature. WHI-779 set the same precedent. **Not a violation.**
- AGENTS.md Status list updated in-place, including a correcting edit to the WHI-779 entry — matches the repo's convention. Test added for the new builder branch.

### Baseline smells (all judgement calls)

**1. Duplicated Code — `web/components/pairs-table.tsx:399-478`.** `CexVsUndCell` and `DexVsUndCell` are near-clones: identical `private` branch, identical dash-fallback shape, identical `.filter(Boolean).join(" · ")` title assembly. One `PremiumCell({ value, rows, label })` with a title-parts array would carry both.

**2. Duplicated Code — the `cex_premium_bps ?? premium_bps` fallback, three sites.** `sort.ts:31`, `pairs-table.tsx:427` (`const value = row.cex_premium_bps ?? row.premium_bps ?? null;`), `pair-detail.tsx:174-175` (twice in one JSX block). Legacy-alias resolution is a domain rule; extract `cexPremiumBps(row)` into `lib/format` — otherwise dropping the alias later is Shotgun Surgery.

**3. Speculative Generality — `web/lib/spread-chart.ts:26,35,73,102,121`.** `rfqPremium` / `hasRfqPremium` are built, typed, and asserted in tests, but no component reads them — the chart plots only `cexPremium` and `ammPremium`; the RFQ-vs-Und figure the UI shows comes from the overview row, not the series. Either plot it or drop the arrays.

**4. Duplicated Code / drift — tooltip text stated twice, divergently.** `COLS` entry `amm_bps` has `title: "AMM mid relative to CEX mid (bps) — core arb spread"` while the matching cell hard-codes `title="AMM mid vs CEX mid (bps)"` (`pairs-table.tsx:729`); same split for `rfq_bps`. Two strings for one concept, already worded differently.

**5. Redundant dependency — `spread-chart.tsx:373-375, 398-400`.** Effects depend on `hasCexPremium`, `hasAmmPremium`, *and* `hasPremium`, which is their disjunction (`spread-chart.ts:122`). Harmless, but it reads as three independent inputs.

**6. Inconsistent naming — `sort.ts`.** New key `amm_premium` next to legacy `premium_bps` for the CEX counterpart; the pair now reads as different concepts. `cex_premium` / `amm_premium` would match `amm_spread` / `rfq_spread`.

**7. Untested new branch.** `sort.ts:31` alias fallback and the `amm_premium` case get no coverage in `sort.test.ts`, though the repo does unit-test pure helpers (AGENTS.md "Web pure-helper unit tests (format/sort)").
