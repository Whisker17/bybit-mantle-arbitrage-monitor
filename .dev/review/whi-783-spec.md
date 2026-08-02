# Spec review — WHI-783

Repo: /Users/whisker/Work/src/personal/bybit-mantle-arbitrage-monitor-whi-783
Fixed point: origin/dev
Diff command: `git diff origin/dev...HEAD`
Commits: `git log origin/dev..HEAD --oneline`
  e320dcc feat(web): split premium vs-Und into CEX/DEX groups (WHI-783)

## Spec (Linear WHI-783) — accept verbatim

Background: WHI-779 put Underlying + Premium (CEX vs und default, AMM/RFQ in hover). User design: equity price is shared reference; distance-to-equity should show inside CEX and DEX groups. Also rename AMM/RFQ bps to name the CEX basis so they don't confuse with vs Und.

Target layout (no Ven column):
| Pair Sess | CEX: Bid Ask Mid Vol [vs Und] | DEX: AMM RFQb RFQs [vs CEX] [RFQ vs CEX] [vs Und] Vol | Edge: Net Dir | Result | Underlying: Price |

Requirements:
1. CEX group: add `vs Und` (bps) = CEX de-multiplied mid vs equity
2. DEX: rename AMM bps → `vs CEX`, RFQ bps → `RFQ vs CEX` (values/calc unchanged; UI labels/tooltips only; keep API field names amm_spread_bps/rfq_spread_bps)
3. DEX group: add `vs Und` (bps) = AMM mid vs equity; RFQ vs equity in hover when RFQ market; no empty RFQ column when no RFQ
4. Underlying group: only reference price (+ price_type badge + as_of); no premium column
5. Detail page: chart series by basis (DEX vs CEX, CEX vs Und, DEX vs Und) aligned with overview
6. Keep: multiplier/rebasing equity-eq math; price_type closed-session labels; do not regress WHI-779 math
7. vs CEX calc/fields unchanged — avoid TUI freeze breakage and API compat
8. API may expose cex_premium_bps / amm_premium_bps / rfq_premium_bps (already present on overview)
9. Acceptance: every column name/tooltip states who-vs-whom; CEX and DEX each have vs equity; vs CEX and vs Und adjacent without confusion; no empty RFQ cols on AMM-only markets; numbers match (equity_eq/und − 1)×10^4; tests green

Brief: Report: (a) requirements the spec asked for that are missing or partial; (b) behaviour in the diff that wasn't asked for (scope creep); (c) requirements that look implemented but where the implementation looks wrong. Quote the spec line for each finding. Under 400 words.

Run the diff yourself in this worktree. Output only the Spec report.
