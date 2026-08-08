# ADR-0002: Overview Net anchored at PnL v2 Q*

* Status: Accepted
* Date: 2026-08-08
* Issue: WHI-965 (M9 cost-model truth-up)
* Owner decision: option **B** (re-anchor overview Net to Q*)

## Context

The live panel exposes two headline size-aware numbers side by side:

| Surface | Size used today | Source |
|---------|-----------------|--------|
| Overview **Net** (`net_edge_bps`) | Fixed M3 reference **$1 000** (`tui.reference_size_usd` = `metrics.breach_size_usd`) | M3 `edge_bps` at that rung of `size_ladder_usd` ($1K / $5K / $20K) |
| Overview **Bucket PnL** | PnL v2 **Q\*** (optimal notional) | Cash-flow engine (`pnl_v2`); live optimum often **~$180–350** |

Measured Fluxion impact on the pools the bot actually trades is roughly
**86–122 bps @ $500** vs **171–243 bps @ $1 000** (bot DESIGN §2.5; M8
edge / delay-decay notes). The bot caps clips at 1 000 USDT and finds the
optimum **often below** that. Evaluating Net at $1 000 therefore prices a
clip size that is **rarely the trade**, systematically heavier wear than
the capturable optimum — while Bucket PnL at Q\* can still be positive.
The two columns routinely **disagree in sign for structural reasons**,
which is hard to trust and easy to misread as a feed bug.

WHI-965 weighed three product responses:

* **A** — Add smaller M3 rungs ($250 / $500) and keep Net on a fixed ladder.
* **B** — Re-anchor overview Net to the same economics as Bucket PnL (Q\*).
* **C** — Keep $1 000 Net and document the structural sign disagreement.

A and C either leave the headline at a non-traded size (C) or still
require choosing a fixed rung that may not match Q\* (A). Changing the
fixed ladder also collides with validators that couple
`reference_size_usd`, `breach_size_usd`, and `size_ladder_usd`
(`monitor/tui/config.py`), and breaks cumulative `EdgeStats`
comparability across history — a trade-off that needed owner sign-off
rather than an implementer config flip.

## Decision

**Overview headline Net is defined at PnL v2 optimal size Q\*, not at the
fixed M3 $1 000 reference.**

Product meaning after this ADR:

1. **Overview Net** answers: *at the size we would actually clip (Q\*),
   what is the paper net edge?* It must use the **same Q\*** (and direction
   selection policy) as the overview Bucket PnL / `pnl_v2` optimal card.
2. When both Net and Bucket PnL are in an **ok** (or `quote_aged`) state
   for the same tick, they **must not disagree in sign** for structural
   size reasons. Residual disagreement is allowed only for orthogonal
   reasons (e.g. one field missing, non-ok status, RFQ-only path with no
   AMM Q\*).
3. The fixed M3 ladder (**$1K / $5K / $20K**) and
   `reference_size_usd: 1000` are **no longer the overview Net
   definition**. They may remain as **secondary fixed-size diagnostics**
   (detail wear waterfall, cost floor breach series, historical
   `EdgeStats` at a documented epoch) until a follow-up retires or
   renames them — but they are not the headline.

This ADR **records the product decision only**. Wiring overview builders,
API fields, Web labels, and any EdgeStats epoch is a **follow-up
implementation issue** (out of scope for WHI-965).

## Consequences

### Positive

* Headline Net and Bucket PnL share one size semantics → sign agreement
  and bot-aligned reading of “is there edge right now?”
* Aligns the panel with measured clip economics and M8 / bot Q\* search
  without pretending $1 000 is the typical trade.
* Avoids expanding the M3 ladder solely to chase a fixed rung that still
  may not equal Q\*.

### Negative / trade-offs

* **Semantic break** for overview Net: historical mental model and any
  external notes that say “Net is always at $1 000” become false after
  the implement PR lands. UI copy must show **Q\*** (notional) next to
  Net so the size is never implicit.
* **`EdgeStats` / breach series** today only accumulate at
  `breach_size_usd` ($1 000). Re-anchoring the *live* Net does not
  automatically make those cumulative distributions Q\*-native. The
  implement issue must choose one of:
  * keep breach stats on the fixed rung and label them as such
    (secondary), or
  * start a new Q\*-keyed series and document an epoch break.
* **Q\* is path-dependent** (depth, AMM curve, caps). Net can move when
  depth updates even if mids are flat — that is correct under this
  decision, but operators need the size chip visible.
* **TUI is frozen** for new features (DESIGN / Agents.md). Implement on
  **Web + API**; TUI may keep $1 000 Net until explicitly unfrozen or
  retired.
* M3 detail ladders and wear breakdowns at fixed Q remain useful; do not
  delete them solely because overview Net moved.

### Implementation guidance (non-normative for WHI-965)

Follow-up work should, at minimum:

* Drive overview `net_edge_bps` (or successor field) from PnL v2 optimal
  net bps at Q\*, same direction as the Bucket PnL card.
* Surface `optimal_notional_usd` / Q\* beside Net on overview.
* Update DESIGN §2.3 / §2.6.3 copy so M3 “Net at $1 000” is no longer
  the overview contract (partial DESIGN pointer lands with this ADR).
* Decide EdgeStats epoch policy explicitly in the implement issue.
* Do **not** silently change `breach_size_usd` without an EdgeStats plan.

## Rejected alternatives

### A — Add $250 / $500 M3 rungs; keep fixed-size Net

Rejected as the **headline** fix: a smaller fixed rung is closer to live
optima but still not Q\*, so Net and Bucket PnL can still disagree when
Q\* is $180 or $900. Ladder / validator / EdgeStats churn without solving
sign agreement. Fixed small rungs may still be added later as **detail**
diagnostics if useful — not required by this decision.

### C — Keep $1 000 Net; document structural sign disagreement

Rejected: documentation alone leaves the most visible column pricing a
size the bot rarely trades, and continues to train distrust of the panel.
Acceptable only if the product wanted “stress Net at a large clip”;
owner preference is capturable-edge truth for the headline.

## References

* Linear: [WHI-965](https://linear.app/whisker-personal/issue/WHI-965)
* DESIGN §2.3 (M3 paper edge), §2.6 (PnL v2), §2.6.3 (size ladders)
* `config/metrics.yaml` `size_ladder_usd` / `breach_size_usd` / `pnl_v2`
* `config/tui.yaml` `reference_size_usd`
* `monitor/tui/config.py` reference↔breach↔ladder validators
* Bot measured impact / clip caps (bot DESIGN §2.5); M8 notes under
  `docs/references/m8-*.md`
