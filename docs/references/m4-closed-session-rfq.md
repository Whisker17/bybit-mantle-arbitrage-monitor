# Closed-session RFQ availability (WHI-753)

Research note: does xChange Atomic RFQ still quote when US equities are closed,
and if so does that break the product assumption that closed hours are
AMM-dominated for *pricing* (not just fills)?

**Collected:** 2026-08-01 (Saturday) ~11:08–11:15 UTC — NYSE weekend, full
closed session. **Open-session control not yet available** (collector had no
prior RTH tape on the research host; VPS `data/monitor.db` was empty at
analysis time). Re-run the open vs closed contrast once ≥1 full RTH day of
`fluxion_rfq_quotes` exists.

## Executive conclusion

1. **RFQ is not RTH-only.** On a Saturday, 5/11 inventory pairs had stable
   **two-sided** RFQ quotes; 2 more had **buy-only**; 4 had none (all 204).
2. **Quotes track Bybit mid**, not a stale weekend mark. Two-sided RFQ mid sat
   within ~0–15 bps of de-multiplied Bybit mid; RFQ bid/ask framed Bybit with
   ~±70 bps wings and ~145 bps full RFQ spread.
3. **Official same-origin and Railway proxy are the same pricing backend.**
   Status always matched; prices exact-equal on 34/36 dual-200 observations
   (max |Δ| 1.6 bps). Distinct `requestId` per URL → independent mint, shared
   inventory/pricing.
4. **HTTP 200 quotes are executable prices per Fluxion’s own skill docs**
   (`amountOut` → LOP `takingAmount`). Response body has **no** signature /
   deadline / order payload — execution still requires the separate
   build/submit path. Panel should keep treating 200 as a live RFQ column, not
   “indicative only.” **No TUI “indicative” annotation issue** is opened from
   this research.
5. **Product impact:** drop the hard rule “closed = AMM alone drifts / RFQ
   absent.” Session still segments *stats*, but mechanism and edge columns must
   keep RFQ whenever `available`. MMs appear to quote **against Bybit 7×24**
   for liquid names.

## Method

| Item | Value |
|------|--------|
| Pair list | `config/pairs.yaml` (11) |
| Legs | `buy_native` (100 USDC exact-in) + `sell_native` (0.1 native exact-in) |
| Endpoints | `POST https://fluxion.network/api/limit-order/quote` and Railway proxy from `rfq.proxy_quote_url` |
| Rate | ~1.1 s between HTTP calls (under 60/min budget) |
| Rounds | 3 full inventory passes, ~20 s idle between rounds |
| Bybit | REST `/v5/market/tickers` mid = (bid1+ask1)/2, de-multiplied by `xstockMultiplier` |
| Tradeability | Response field inventory + Fluxion-trade-skill `references/xstock-rfq.md` |

Original ticket claimed tape already on VPS
`/root/dev/bybit-mantle-arbitrage-monitor/data/monitor.db`; at research time
that path had an empty `data/` (collector not running). Analysis uses the
live multi-round sample above instead.

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
stayed 200 all three rounds (same for 204). Refresh cadence is therefore
“stable inventory, re-minted quote each poll,” not random 204 flicker in this
sample. Longer closed-session tails (collector tape) can still show sporadic
204s as the ticket noted.

## 2. Spread width vs Bybit mid

Mean over three rounds for pairs with data. RFQ prices from primary 200 bodies;
Bybit mid de-multiplied.

| Pair | Bybit mid (native) | RFQ buy vs mid (bps) | RFQ sell vs mid (bps) | RFQ spread (bps) | RFQ mid vs Bybit (bps) |
|------|--------------------|----------------------|-----------------------|------------------|-------------------------|
| AAPLx | ~307.6 | **+64** | **−80** | **+145** | **−8** |
| GOOGLx | ~353.6 | **+85** | **−60** | **+144** | **+13** |
| METAx | ~554.7 | **+75** | **−68** | **+143** | **+4** |
| NVDAx | ~199.9 | **+72** | **−74** | **+147** | **−1** |
| TSLAx | ~309.7 | **+74** | **−70** | **+144** | **+2** |
| CRCLx | — | **+71** (buy only) | n/a | n/a | n/a |
| HOODx | — | **+75** (buy only) | n/a | n/a | n/a |

Reading:

- Two-sided **RFQ mid ≈ Bybit mid** (usually <10 bps; GOOGL ~+13).
- Full RFQ book is ~**1.4–1.5%** wide — large vs Bybit L1, relevant for paper
  edge (RFQ wear already separate from AMM fee path).
- Buy-only names still post an ask wing ~70 bps over Bybit mid; no sell
  inventory in this sample.

## 3. Tracking / lag vs Bybit (closed)

Across three rounds (~5 min between first and last pass), two-sided pairs
co-moved with Bybit at the few-bps scale:

| Pair | Δ RFQ buy (bps) | Δ RFQ sell (bps) | Δ Bybit mid (bps) |
|------|-----------------|------------------|-------------------|
| AAPLx | +2.9 | +2.6 | +4.7 |
| NVDAx | +3.5 | +3.5 | +6.5 |
| GOOGLx | −3.7 | −3.7 | −0.3 |
| METAx | ~0 | ~0 | −0.7 |
| TSLAx | ~0 | ~0 | +1.1 |

`createdAt` was unique per successful poll (36 distinct timestamps). MMs (or
the RFQ service) re-price frequently enough that weekend RFQ is **live relative
to Bybit**, not a Friday freeze. True lead–lag (cross-correlation over hours)
needs a continuous collector tape — out of scope for this one-shot sample.

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
| Indicative-only display? | **No** per vendor docs — 200 is an executable quote price. |
| Firm pre-signed fill you can submit as-is? | **No** — still need order build/submit (out of v1 panel scope). |
| TUI “indicative” badge needed? | **No** for this finding. Keep 204 → unavailable; 200 → RFQ column. |
| Quote TTL field? | Only `createdAt`. Staleness = collector poll age (already in ticks). |

## 6. Implications for DESIGN / M4

### Superseded assumption

Earlier product language (DESIGN §1.2): “RFQ dominates open hours, AMM
dominates closed hours.” That mixed **fill regime** (unverified for closed
hours without RTH tape) with **quote regime**.

**Revised:**

- **Quote regime (measured, closed/weekend):** liquid pairs keep two-sided RFQ
  quotes that track Bybit; thin pairs may be one-sided or dark.
- **Fill regime:** still unknown without open vs closed LOP/`fluxion_rfq_fills`
  + AMM swap counts. Do not infer “no RFQ fills when closed” from quotes alone.
- **Mechanism layer (M4):** remains **fill-sourced** — LOP fill → `rfq`, pool
  Swap → `amm` — **independent of session**. Session is a **segmentation axis**,
  not a proxy for mechanism.
- **Paper edge:** when RFQ is `available` in closed hours, net edge vs RFQ is
  still a first-class column (same as open). Closed-session edge must not
  silently drop RFQ and only score AMM.

### M4 rule clarifications (see also `m4-attribution-labels.md`)

1. Do **not** encode “closed session ⇒ mechanism = AMM only.”
2. `activity_regime` (`all_hours` / `rth_only`) remains a **behavior** feature
   on AMM takers; it does not mean RFQ is off outside RTH.
3. Closed-session aggregates with RFQ fills (when pair_id enrichment lands)
   are valid evidence of **MM settlement outside RTH**, not anomalies.

## 7. Open / deferred measurements

| Item | Why deferred |
|------|----------------|
| Open vs closed coverage contrast on the same pairs | Needs ≥1 NYSE RTH day of `fluxion_rfq_quotes` on a running collector |
| RFQ fill rate open vs closed | Needs `fluxion_rfq_fills` volume + pair enrichment (see DEFERRED_ISSUES) |
| Multi-hour lag stats (Bybit lead → RFQ) | Needs continuous tape, not 3 rounds |
| Official marketing claim “closed = AMM only” | Not restated in-repo; if seen externally, this note is the counter-evidence for **quotes** |

## Sources

- Live RFQ HTTP samples 2026-08-01 (this note’s tables).
- `config/pairs.yaml` RFQ URLs and pair inventory.
- `docs/references/m1-rfq-feasibility.md` (pollable_quote mode).
- Fluxion-trade-skill: https://github.com/Fluxion-Exchange/Fluxion-trade-skill
  `references/xstock-rfq.md` (204/200 semantics, amountOut executable).
- Bybit public spot tickers API (mid construction).
