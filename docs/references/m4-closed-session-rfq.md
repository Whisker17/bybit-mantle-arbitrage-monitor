# Closed-session RFQ availability (WHI-753)

Research note: does xChange Atomic RFQ still quote when US equities are closed,
and if so does that break the product assumption that closed hours are
AMM-dominated for *pricing* (not just fills)?

**Collected:** 2026-08-01 (Saturday) ~11:08–11:15 UTC — NYSE weekend, full
closed session. **Open-session control not yet available** (collector had no
prior RTH tape on the research host; VPS `data/monitor.db` was empty at
analysis time). Open vs closed contrast + multi-hour lag deferred to follow-up
(see §7 / Linear / `docs/DEFERRED_ISSUES.md`).

**Reproducible artifact:** one later same-day slim pass is checked in as
`docs/references/m4-closed-session-rfq-sample.json` (statuses, prices, Bybit
mids, endpoint parity). **Multi-round mean tables in §1–§3 are from the earlier
3-round pass (~11:08 UTC)** and are not recomputed from the JSON (a later slim
pass will differ by a few bps); the sample re-confirms the same coverage
pattern and endpoint parity.

## Executive conclusion

1. **RFQ is not RTH-only.** On a Saturday, 5/11 inventory pairs had stable
   **two-sided** RFQ quotes; 2 more had **buy-only**; 4 had none (all 204).
2. **Quotes track Bybit mid**, not a stale weekend mark. Two-sided RFQ mid sat
   within ~0–15 bps of de-multiplied Bybit mid; RFQ bid/ask framed Bybit with
   ~±70 bps wings and ~144–147 bps full RFQ spread (see table note on rounding).
3. **Official same-origin and Railway proxy are the same pricing backend.**
   Status always matched; prices exact-equal on 34/36 dual-200 observations
   (max |Δ| 1.6 bps). Distinct `requestId` per URL → independent mint, shared
   inventory/pricing.
4. **Tradeability is vendor-asserted, not fill-verified.** Fluxion skill docs
   call HTTP 200 + `amountOut` an **executable** quote for LOP build; body has
   **no** signature / deadline / order payload (execution still needs
   build/submit). This research does **not** open a TUI “indicative-only”
   annotation issue, but firmness is **not** proven by an on-chain closed-session
   fill in this sample (see DEFERRED_ISSUES RFQ fill enrichment / topic0).
5. **Product impact:** drop the hard rule “closed = AMM alone drifts / RFQ
   absent.” Session still segments *stats*. Mechanism stays fill-sourced (M4
   rule of record: `m4-attribution-labels.md`). MMs appear to quote **against
   Bybit outside RTH** for liquid names.

## Method

| Item | Value |
|------|--------|
| Pair list | `config/pairs.yaml` (11) |
| Legs | `buy_native` (100 USDC exact-in) + `sell_native` (0.1 native exact-in) — **same notional as collector**, not the M3 $1K/$5K/$20K ladder |
| Endpoints | `POST https://fluxion.network/api/limit-order/quote` and Railway proxy from `rfq.proxy_quote_url` |
| Rate | ~1.1 s between HTTP calls (~55 req/min sustained when alternating primary+proxy — under the 60/min budget with thin headroom; research only, not a collector change) |
| Rounds | 3 full inventory passes, ~20 s idle between rounds (~7 min wall) |
| Bybit | REST `/v5/market/tickers` mid = (bid1+ask1)/2, de-multiplied by `xstockMultiplier` |
| Tradeability | Response field inventory + Fluxion-trade-skill `references/xstock-rfq.md` |

Original ticket claimed tape already on VPS
`/root/dev/bybit-mantle-arbitrage-monitor/data/monitor.db`; at research time
that path had an empty `data/` (collector not running). Analysis uses the
live multi-round sample above instead.

**Notional caveat:** coverage and spreads are for ~$100 / 0.1 native polls.
Panel edge ladder sizes may see different RFQ availability (already tracked as
**RFQ poll notional ≠ edge ladder** in `docs/DEFERRED_ISSUES.md`).

## 1. Coverage (closed session)

Primary endpoint, 3 rounds × 11 pairs × 2 legs = **66** leg polls.

| Metric | Value |
|--------|-------|
| HTTP 200 | 36 / 66 (**54.5%**) |
| HTTP 204 | 30 / 66 (**45.5%**) |
| Status primary ≠ proxy | **0** |

### Per pair (primary; counts out of 3 rounds)

| Pair | Buy 200 | Sell 200 | Two-sided rounds | Pattern |
|------|---------|----------|------------------|---------|
| AAPLx | 3/3 | 3/3 | 3 | two-sided |
| GOOGLx | 3/3 | 3/3 | 3 | two-sided |
| METAx | 3/3 | 3/3 | 3 | two-sided |
| NVDAx | 3/3 | 3/3 | 3 | two-sided |
| TSLAx | 3/3 | 3/3 | 3 | two-sided |
| CRCLx | 3/3 | 0/3 | 0 | buy-only |
| HOODx | 3/3 | 0/3 | 0 | buy-only |
| SPCXx | 0/3 | 0/3 | 0 | none |
| AMZNx | 0/3 | 0/3 | 0 | none |
| COINx | 0/3 | 0/3 | 0 | none |
| MCDx | 0/3 | 0/3 | 0 | none |

Matches the ticket’s Saturday spot check (liquid megacaps two-sided; HOOD/CRCL
buy-only; SPCX dark) and extends it to the full M1 set.

No intermittent flip-flop within the ~7 min window: a pair×leg that was 200
stayed 200 all three rounds (same for 204). Longer closed-session tails
(collector tape) can still show sporadic 204s as the ticket noted.

### Refresh / re-mint (what we can say from 3 rounds)

| Observation | Evidence |
|-------------|----------|
| New quote mint each poll | 36 unique `createdAt` / 36 successful primary 200s |
| Same-leg status sticky | No 200↔204 flips across rounds for any pair×leg |
| Price change rate | See §3: two-sided names moved 0–4 bps over ~5 min when Bybit moved |

This is **re-mint cadence** (every poll gets a new `requestId`/`createdAt`), not
a measured MM refresh interval independent of our poll schedule. Collector
cadence remains `min_poll_interval_s` / round-robin from `pairs.yaml`.

## 2. Spread width vs Bybit mid

Mean over three rounds for pairs with data. RFQ prices from primary 200 bodies;
Bybit mid de-multiplied. **RFQ spread** is \((P_\mathrm{buy}-P_\mathrm{sell}) /
\mathrm{mid}(P_\mathrm{buy},P_\mathrm{sell})\) in bps — not equal to
\(|\mathrm{buy\_vs}|+|\mathrm{sell\_vs}|\) when those wings use Bybit mid as
denominator (hence ~1 bps table residuals after rounding).

| Pair | Bybit mid (native) | RFQ buy vs mid (bps) | RFQ sell vs mid (bps) | RFQ spread (bps) | RFQ mid vs Bybit (bps) |
|------|--------------------|----------------------|-----------------------|------------------|-------------------------|
| AAPLx | ~307.6 | **+64.0** | **−80.5** | **+144.7** | **−8.2** |
| GOOGLx | ~353.6 | **+84.7** | **−59.6** | **+144.1** | **+12.5** |
| METAx | ~554.7 | **+75.4** | **−67.5** | **+142.8** | **+3.9** |
| NVDAx | ~199.9 | **+72.4** | **−74.1** | **+146.5** | **−0.9** |
| TSLAx | ~309.7 | **+74.1** | **−70.2** | **+144.3** | **+1.9** |
| CRCLx | — | **+70.5** (buy only) | n/a | n/a | n/a |
| HOODx | — | **+74.7** (buy only) | n/a | n/a | n/a |

Reading:

- Two-sided **RFQ mid ≈ Bybit mid** (usually <10 bps; GOOGL ~+13).
- Full RFQ book is ~**1.4–1.5%** wide — large vs Bybit L1, relevant for paper
  edge (RFQ wear already separate from AMM fee path).
- Buy-only names still post an ask wing ~70 bps over Bybit mid; no sell
  inventory in this sample.

## 3. Tracking vs Bybit (closed) — co-movement, not lag

Across three rounds (~5 min between first and last pass), two-sided pairs
**co-moved** with Bybit at the few-bps scale. This is **not** a measured
lead–lag / cross-correlation (sample too short; no continuous tape).

| Pair | Δ RFQ buy (bps) | Δ RFQ sell (bps) | Δ Bybit mid (bps) |
|------|-----------------|------------------|-------------------|
| AAPLx | +2.9 | +2.6 | +4.7 |
| NVDAx | +3.5 | +3.5 | +6.5 |
| GOOGLx | −3.7 | −3.7 | −0.3 |
| METAx | ~0 | ~0 | −0.7 |
| TSLAx | ~0 | ~0 | +1.1 |

Interpretation: weekend RFQ is **live relative to Bybit**, not a Friday freeze.
Multi-hour lag stats remain deferred (§7).

## 4. Endpoint parity (official vs Railway)

| Check | Result |
|-------|--------|
| Status agreement | 66/66 |
| Dual HTTP 200 | 36 |
| Exact `price` / `amountOut` match | **34/36** |
| Max \|price Δ\| when both 200 | **1.6 bps** (mean \|Δ\| ~0.05 bps) |
| Same `requestId` | **0/36** |

Interpretation: both URLs are front doors to the same quote service / MM book.
Prefer `fluxion.network` same-origin (already collector default); fall back to
Railway. Do **not** treat proxy as a second independent MM.

## 5. Tradeability (signature / TTL)

### Observed response keys (union of 200 bodies)

`side`, `chainId`, `amountIn`, `amountOut`, `price`, `requestId`, `tokenIn`,
`tokenOut`, `createdAt`, `rawPrice`, `rawAmountOut`.

**Absent:** signature, EIP-712 typed data, maker, r/s/v, deadline, expiry,
`orderHash`, nested `order` object.

### Official semantics (Fluxion-trade-skill `xstock-rfq.md`)

- HTTP **204** → “no **executable** RFQ at that moment.”
- HTTP **200** → validate chain/tokens; treat **`amountOut` as the executable
  quoted output** for LOP build (`takingAmount`); `raw*` is pre-adjustment
  reference only; preserve `requestId` for diagnostics, not order payload.
- RFQ service already applies ~5 bps execution tolerance into `amountOut`.

### Panel implication

| Question | Answer |
|----------|--------|
| Vendor: indicative-only? | **No** — skill docs treat 200 `amountOut` as executable for LOP build. |
| Firm pre-signed fill you can submit as-is? | **No** — still need order build/submit (out of v1 panel scope). |
| Fill-verified firmness this weekend? | **Not measured** — no closed-session LOP fill in sample. |
| TUI “indicative” badge needed? | **Not opened** (vendor-asserted executable). Revisit if fills never appear while quotes stay 200. |
| Quote TTL field? | Only `createdAt`. Staleness = collector poll age (already in ticks). |
| Live column gate | Match `FluxionRfqQuoteTick.available` (HTTP 200 and price present). |

## 6. Implications for DESIGN / M4

### Superseded assumption

Earlier product language (DESIGN §1.2): “RFQ dominates open hours, AMM
dominates closed hours.” That mixed **fill regime** (unverified for closed
hours without RTH tape) with **quote regime**. DESIGN §1.2 / §8 and this note
now state the quote-regime correction; **normative M4 mechanism rules** live
only in `m4-attribution-labels.md` § “Session is not mechanism (WHI-753)”.

**Quote regime (measured, closed/weekend):** liquid pairs keep two-sided RFQ
quotes that track Bybit mid; thin pairs may be one-sided or dark.

**Fill regime:** still unknown without open vs closed LOP/`fluxion_rfq_fills`
+ AMM swap counts. Do not infer “no RFQ fills when closed” from quotes alone.

## 7. Open / deferred measurements

| Item | Owner |
|------|--------|
| Open vs closed RFQ coverage + fill-rate contrast | [WHI-760](https://linear.app/whisker-personal/issue/WHI-760); needs running collector through ≥1 RTH day |
| Multi-hour Bybit→RFQ lag / correlation | WHI-760; continuous `fluxion_rfq_quotes` + `bybit_book` |
| RFQ fill pair enrichment / topic0 live confirm | Already in `docs/DEFERRED_ISSUES.md` (WHI-730/731) |
| Ladder-notional RFQ polls | Already in `docs/DEFERRED_ISSUES.md` (WHI-732) |

## Sources

- Live RFQ HTTP samples 2026-08-01 (this note’s tables +
  `docs/references/m4-closed-session-rfq-sample.json`).
- `config/pairs.yaml` RFQ URLs and pair inventory.
- `docs/references/m1-rfq-feasibility.md` (pollable_quote mode).
- Fluxion-trade-skill: https://github.com/Fluxion-Exchange/Fluxion-trade-skill
  `references/xstock-rfq.md` (204/200 semantics, amountOut executable).
- Bybit public spot tickers API (mid construction).
