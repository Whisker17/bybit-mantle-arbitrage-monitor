# Hummingbot CEX⇄AMM PnL methodology → PnL v2 spec (WHI-754)

Research note for **PnL v2**: extract Hummingbot’s arbitrage profitability math,
map it onto Bybit ⇄ Fluxion xStocks, and pin formulas implementable by WHI-756
without re-opening product decisions.

**Normative product section:** `docs/DESIGN.md` §2.6 (PnL v2). This file is the
derivation and Hummingbot对照; when they disagree, DESIGN wins for product, and
this note must be patched.

**Hummingbot revision read:** `github.com/hummingbot/hummingbot` @
`2bfaccc48dd49e71a5b6d9b3011808e127dd00cd` (master, 2026-07-30). Paths below
are relative to that tree.

---

## 1. Scope

| In | Out |
|----|-----|
| Paper two-sided inventory PnL at size \(Q\) (USD) | Live order placement / balance locks |
| Bybit taker + multi-level VWAP | Maker rebates, VIP fee tiers |
| Fluxion AMM V3 exact-in math (existing `monitor.metrics.amm_slip`) | Multi-hop / router paths |
| RFQ EXACT_INPUT at **reference poll sizes only** | Dense RFQ size ladder (rate-limited) |
| Fixed Mantle gas USD per Fluxion leg | L2 data-fee recompute every block |
| Optimal-size search algorithm for WHI-756 | Execution path / inventory rebalance |

Product inventory model (unchanged from DESIGN §1.2 / M3): base is **pre-positioned
on both venues**. A paper arb sells existing base on the rich venue and buys base
on the cheap venue. No cross-venue transfer cost is in the cash-flow.

---

## 2. Hummingbot methodology (primary sources)

### 2.1 Legacy strategy: `hummingbot/strategy/amm_arb/`

**Proposal construction** (`utils.py` → `create_arb_proposals`):

1. Fix a **base amount** `order_amount` (not USD notional).
2. For each direction of `(is_buy_on_m1 ∈ {true, false})`, quote both markets
   with **opposite sides** so one buy + one sell of the same base amount:
   - `get_quote_price(pair, is_buy, amount)` — expected average fill (VWAP-like).
   - `get_order_price(pair, is_buy, amount)` — limit/market order price to submit.
3. Attach `extra_flat_fees` (gateway `network_transaction_fee` when present) per side.
4. Drop the proposal if any quote is `None`.

**Profit %** (`data_types.py` → `ArbProposal.profit_pct`):

```text
buy_side  = the side with is_buy=True
sell_side = the side with is_buy=False

# Fees converted into the *buy* venue’s quote asset:
buy_fee  = fee_amount_in_token(..., token=buy_quote)
sell_fee = fee_amount_in_token(..., token=sell_quote)

buy_spent_net  = (buy_side.amount  * buy_side.quote_price)  + buy_fee
sell_gained_net = (sell_side.amount * sell_side.quote_price) - sell_fee

# Quote-asset conversion (RateOracle), base assumed 1:1 after interchangeability:
sell_gained_in_buy_quote = sell_gained_net * sell_quote_to_buy_quote_rate

profit_pct = (sell_gained_in_buy_quote - buy_spent_net) / buy_spent_net
```

Properties that matter for us:

| Property | Hummingbot choice | Implication |
|----------|-------------------|-------------|
| Size variable | Fixed **base** amount | Profit is a % of **buy notional**, not of mid |
| Fee sign | Buy fee **adds to cost**; sell fee **subtracts from proceeds** | Never apply the same % fee once as “wear on mid” and again on notional |
| Fee class | `AddedToCostTradeFee` vs `DeductedFromReturnsTradeFee` | Spot taker % is modeled as cost-add on buys (or return-deduct depending on schema); `fee_amount_in_token` normalizes both to a single quote amount before PnL |
| Gas / network | Flat fee token amount via `extra_flat_fees` / `network_transaction_fee` | Same slot as exchange flat fees; converted into quote via RateOracle |
| Gate | `profit_pct >= min_profitability` | Hard threshold in fraction (e.g. `0.003` = 30 bps), not bps-of-mid after wear |
| Slippage buffer | Applied **after** profitability filter, to `order_price` only | PnL uses `quote_price`; buffer is fill probability, not expected edge |

**Main loop guards** (`amm_arb.py` → `main`):

1. Build proposals at configured `order_amount`.
2. Filter by `min_profitability` with `account_for_fee=True`.
3. `apply_slippage_buffers` — bump buy order price up / sell price down.
4. `apply_budget_constraint` — zero amount if free balance < required collateral.
5. Execute; gateway (EVM) side prioritized first so chain latency does not strand CEX.

### 2.2 Order book VWAP (`core/data_type/order_book.pyx`)

Given base volume \(v\):

- **`get_vwap_for_volume(is_buy, volume)`** — walk asks (buy) or bids (sell);
  partial last level; return average price. Query result also carries filled
  volume (may be `< volume` if book is thin — caller must treat that as unfillable).
- **`get_price_for_volume`** — marginal (last) price only, not average.
- **`get_quote_volume_for_base_amount`** — Σ(price × base) for a base amount
  (= notional spend/receive at VWAP without dividing).

Walk is **base-amount sized**, not quote-notional sized. Our M3
`book_vwap_slip_bps` walks by **USD notional** (`size_usd`); both are valid if
levels are `(price, size_base)` and conversion is consistent (see §4.2).

### 2.3 Fee model (`core/data_type/trade_fee.py`)

- **Percent fee** of `price * order_amount` (quote notional of the leg).
- **`fee_amount_in_token(...)`** converts percent + flat fees into a chosen
  reporting token (quote asset for arb PnL).
- **`AddedToCostTradeFee`**: fee increases collateral required (buy-side
  accounting). **`DeductedFromReturnsTradeFee`**: fee reduces proceeds
  (sell-side accounting). PnL uses the unified `fee_amount_in_token` path so
  the sign is applied explicitly in `profit_pct` (+buy / −sell), not by class
  magic alone.
- Interchangeability table treats wrapped natives and some stables as 1:1;
  **v2 executor additionally treats any pair of tokens both containing `"USD"`
  as interchangeable** (USDT/USDC ≈ 1). That is the Hummingbot precedent for
  our default `usdt_usdc_basis_bps: 0`.

### 2.4 Budget (`connector/budget_checker.py`)

`BudgetChecker.adjust_candidates` populates collateral, locks balances across a
batch of hypothetical orders, and optionally resizes (`all_or_none=False`) or
zeros the order. **Paper panel does not implement this** — we report unfillable /
depth-limited sizes instead of wallet-limited sizes. Inventory assumption
already implies “enough base and quote on both sides for the paper size.”

### 2.5 Strategy v2: `controllers/generic/arbitrage_controller.py` + executor

Controller sets:

- Fixed **quote budget** `total_amount_quote` → `order_amount = quote / rate`
  (base), quantized per venue.
- `min_profitability` (fraction).
- `delay_between_executors`, `max_executors_imbalance` (execution cadence /
  inventory drift caps — product non-goals for the panel, but useful as
  **display / alert** ideas).
- Gas token discovery for AMM connectors; `gas_conversion_price` passed into
  the executor.

Executor profitability (`strategy_v2/executors/arbitrage_executor/arbitrage_executor.py`):

```text
buy_price, sell_price = get_quote_price(buy), get_quote_price(sell)   # size-aware
normalized_sell = sell_price * quote_conversion_rate                 # sell quote → buy quote
trade_pnl_pct = (normalized_sell - buy_price) / buy_price

tx_cost = buy_fee_in_base + sell_fee_in_base   # CEX % fees + AMM gas/base
# gas on AMM: network_transaction_fee.amount / gas_conversion_price  (→ base units)

current_profitability = (trade_pnl_pct * order_amount - tx_cost) / order_amount
#                     = trade_pnl_pct - tx_cost / order_amount
```

Gate: `current_profitability > min_profitability`.

**v1 vs v2口径对照:**

| | amm_arb (v1) | arbitrage_executor (v2) |
|--|--------------|-------------------------|
| Gross | (sell_notional − buy_notional) / buy_notional with fee-adjusted notionals | (sell_px − buy_px) / buy_px, fees separate as `tx_cost` |
| Fee fold-in | Inside `profit_pct` via fee-adjusted cash | Percent fees + gas converted to **base**, then subtracted as `tx_cost/order_amount` |
| Gas | `extra_flat_fees` on the AMM side inside fee amount | Explicit `network_transaction_fee` → base via `gas_conversion_price` |
| Size | Config base amount | Quote budget → base via oracle |

Both are **percent-of-buy-notional** profitability at a **fixed size**. Neither
searches for optimal size; the operator picks `order_amount`.

---

## 3. What we keep / drop for Bybit ⇄ Fluxion

| Hummingbot idea | Keep? | Our form |
|-----------------|-------|----------|
| Same base amount on both legs | **Yes** | Size \(Q\) USD → base \(q = Q / P_b^{\mathrm{mid}}\) |
| Fee on buy adds cost; fee on sell cuts proceeds | **Yes** | Explicit cash-flow (§4) |
| Size-aware quote prices (VWAP / AMM) | **Yes** | Bybit depth VWAP; Fluxion V3 math / RFQ poll |
| `min_profitability` gate | **Yes (display)** | `min_profit_usd` / `min_profit_bps` config; do not hide negative PnL |
| Quote conversion oracle | **Partial** | USDT/USDC default 1:1; optional `usdt_usdc_basis_bps` wear (M3) |
| BudgetChecker | **No** | Paper inventory; depth → `fillable=False` |
| Slippage buffer on order price | **No** (no orders) | Optional future “execution haircut” config, default 0 |
| Executor imbalance / delay | **No** (no execution) | Optional session stats only |
| Gas as flat fee in native | **Yes** | Fixed `gas_usd_per_swap` (config); convert via USD not bps-only |

---

## 4. PnL v2 formal definition (implementable)

### 4.1 Symbols and units

| Symbol | Unit | Meaning |
|--------|------|---------|
| \(Q\) | USD | Target single-trade **notional** at Bybit de-multiplied mid |
| \(P_b^{\mathrm{mid}}\) | quote/base | Bybit mid after `de_multiplied_price` (USDT per 1 native-equivalent base) |
| \(q\) | base | \(q = Q / P_b^{\mathrm{mid}}\) — economic base amount (native xStock units) |
| \(m\) | — | Bybit `xstockMultiplier`; applied **only** when converting raw Bybit book prices → comparable prices (already done on ticks). Depth levels used in VWAP must be **de-multiplied prices** with sizes in the same unit as \(q\) (see §4.2) |
| \(f_b\) | fraction | Bybit taker fee = \(10\,\mathrm{bps} = 0.001\) (config `bybit_taker_fee_bps`) |
| \(f_p\) | fraction | AMM pool fee (e.g. 3000 → 0.003); RFQ: **0** (embedded in quote) |
| \(G\) | USD | Mantle gas for **one** Fluxion leg (`gas_usd_per_swap`, default 0.01) |
| \(\beta\) | fraction | Optional USDT/USDC basis wear (`usdt_usdc_basis_bps` / 1e4); default 0 |
| \(D\) | enum | `buy_fluxion_sell_bybit` (BFSB) or `buy_bybit_sell_fluxion` (BBSF) |
| \(V\) | enum | Venue on Fluxion: `amm` or `rfq` |

**Sign convention:** \(\mathrm{PnL}(Q) > 0\) means paper profit in USD after fees and gas.

**No truncation:** if gas or fees dominate, \(\mathrm{PnL}\) is **negative** and reported as-is (WHI-756).

### 4.2 Bybit leg — VWAP + taker fee

Inputs: ordered book side levels \(\{(p_i, s_i)\}\) with **de-multiplied** price
\(p_i\) (USDT per base) and size \(s_i\) in **base** units consistent with \(q\).

Walk (Hummingbot `get_vwap_for_volume` style; equivalent to M3
`book_vwap_slip_bps` when sizing by notional \(Q \approx \sum p\cdot\Delta s\)):

```text
remaining = q
cost_or_proceeds = 0
for (p, s) in levels:           # asks if buying, bids if selling
    take = min(s, remaining)
    cost_or_proceeds += take * p
    remaining -= take
    if remaining <= 0: break
if remaining > 0: UNFILLABLE
P_vwap = cost_or_proceeds / q
```

**Cash after taker fee** (fee in quote, Bybit spot default):

| Leg | Gross quote | Net quote (USDT) |
|-----|-------------|------------------|
| Buy base | \(q \cdot P_{\mathrm{ask}}^{\mathrm{vwap}}\) | \(\mathrm{USDT_{out}} = q \cdot P_{\mathrm{ask}}^{\mathrm{vwap}} \cdot (1 + f_b)\) |
| Sell base | \(q \cdot P_{\mathrm{bid}}^{\mathrm{vwap}}\) | \(\mathrm{USDT_{in}} = q \cdot P_{\mathrm{bid}}^{\mathrm{vwap}} \cdot (1 - f_b)\) |

Notes:

1. Fee is **not** double-counted with half-spread: VWAP already sits on the
   trade side of mid; fee is a separate percent of that notional (Hummingbot
   `fee_amount_in_token` on quote).
2. Without depth, degrade to L1: \(P_{\mathrm{ask}}^{\mathrm{vwap}} = \mathrm{ask}_1\),
   \(P_{\mathrm{bid}}^{\mathrm{vwap}} = \mathrm{bid}_1\) (M3 today). Mark
   `bybit_depth_source = l1` vs `book`.
3. **Multiplier step:** de-multiply **before** VWAP. Never de-multiply VWAP
   after walking raw levels with native sizes (that would mismatch \(q\)).

### 4.3 Fluxion leg — AMM vs RFQ

#### 4.3.1 AMM (V3, exact)

Reuse `monitor.metrics.amm_slip` pure math (fee separated from impact):

- **Buy base (exact-in quote):** choose quote input so that base out \(= q\), **or**
  (engine convenience) exact-in with `size_usd = Q` then set \(q_{\mathrm{eff}} = \mathrm{amount\_out}\)
  and use **the same** \(q_{\mathrm{eff}}\) on Bybit.  
  **Normative for v2 buckets:** fix \(q = Q / P_b^{\mathrm{mid}}\), solve AMM for
  quote needed to buy \(q\) (exact-out) when available; if only exact-in is
  implemented, binary-search quote_in until base_out matches \(q\) within
  `q_tol` (e.g. \(10^{-12}\) relative), failing → unfillable.
- **Sell base:** exact-in base \(q\) → quote out.

Pool fee: either folded into AMM amounts (UniV3 applies fee on input) **or**
reported as a wear line. **PnL cash-flow uses fee-inclusive amounts** (what you
actually pay/receive). Wear breakdown for the UI may still split
`fluxion_fee_usd` vs `fluxion_impact_usd` like M3’s bps split — but
\(\mathrm{PnL}\) must use one consistent fee-inclusive path (do not subtract
pool fee twice).

Gas: subtract \(G\) USD once per Fluxion leg (one swap).

#### 4.3.2 RFQ (reference sizes only)

- Poll EXACT_INPUT at 1–2 configured notionals (e.g. $100 / $1 000 USDC) per
  side; global budget 60 req/min (see `m1-rfq-feasibility.md`).
- Executable price is the poll response; **no extra pool fee line**.
- For bucket \(Q\) ≠ poll notional: **do not invent RFQ impact**. Either:
  - mark RFQ cell `n/a` / `stale_size`, or
  - show **indicative** PnL using the nearest poll price with flag
    `rfq_size_mismatch=true` (M3 status quo for ladder).  
  WHI-756: RFQ is a **对照列** at reference sizes, not a full 6-bucket table.

### 4.4 Direction cash-flows (USD)

Treat USDT and USDC as USD with optional basis:

\[
\mathrm{usd}(x_{\mathrm{USDT}}) = x_{\mathrm{USDT}},\quad
\mathrm{usd}(x_{\mathrm{USDC}}) = x_{\mathrm{USDC}}\cdot(1 - \beta)
\]

(With default \(\beta=0\), both are 1:1. \(\beta>0\) means “Bybit USDT richer”
wear applied by shrinking USDC value of Fluxion cash — same spirit as M3
additive basis bps; implementers may equivalently add \(\beta\cdot Q\) as a
wear term. Document the chosen encoding in code comments; tests lock one.)

#### Direction BFSB — buy Fluxion, sell Bybit

```text
USDC_spent  = quote_in_to_buy_q_on_fluxion(q)     # fee-inclusive
USDT_recv   = q * P_bid_vwap * (1 - f_b)
PnL_USD     = usd(USDT_recv) - usd(USDC_spent) - G
```

#### Direction BBSF — buy Bybit, sell Fluxion

```text
USDT_spent  = q * P_ask_vwap * (1 + f_b)
USDC_recv   = quote_out_from_sell_q_on_fluxion(q) # fee-inclusive
PnL_USD     = usd(USDC_recv) - usd(USDT_spent) - G
```

**RFQ variants:** replace Fluxion quote_in/out with RFQ `amountIn`/`amountOut`
at the matched reference size; \(G\) still applies if settlement is on-chain
(LOP fill burns gas). Panel default: charge \(G\) for both AMM and RFQ
Fluxion legs (conservative). Config `gas_on_rfq: true` (default true).

### 4.5 Relation to M3 `edge_bps`

M3:

```text
edge_bps = gross_spread_bps - Σ wear_bps(Q)
```

with wear in bps of Bybit mid and L1 slip by default.

PnL v2:

```text
PnL_USD(Q) ≈ (edge_bps / 1e4) * Q
```

**only when** (a) both legs are priced vs the same mid, (b) slip/fee models match,
(c) gas_bps = G/Q*1e4. VWAP + exact AMM break this equality at large \(Q\).
**WHI-756 must compute cash-flow PnL directly**, not via `edge_bps * Q`.
M3 remains the live TUI path until a later issue swaps the overview column.

Inverse (for dashboards that still want bps):

```text
pnl_bps(Q) = PnL_USD(Q) / Q * 1e4     # undefined if Q=0
```

### 4.6 Gas erosion (small buckets)

\[
\mathrm{gas\_bps}(Q) = \frac{G}{Q}\cdot 10^4
\]

With \(G = 0.01\):

| \(Q\) | gas bps | Implication |
|------:|--------:|-------------|
| $10 | 10.0 | Needs \>10 bps gross-after-fees just to break even on gas alone |
| $50 | 2.0 | Material vs 10 bps Bybit fee |
| $100 | 1.0 | Still visible |
| $500 | 0.2 | Small |
| $1 000 | 0.1 | Negligible vs fee/slip |
| $10 000 | 0.01 | Noise |

**Always-negative region:** if fee floor alone exceeds gross,

\[
f_b + f_p + \beta + \mathrm{slip}(Q) + G/Q > \mathrm{gross\_frac}(Q)
\]

then \(\mathrm{PnL}<0\). For tiny \(Q\), \(G/Q\) dominates even when mids match
(gross≈0). Spec requires reporting that negative number, not clamping to 0.

---

## 5. Optimal size search (for WHI-756)

### 5.1 Objective

For each \((\mathrm{pair}, D, V=\mathrm{amm})\) at a snapshot:

\[
Q^\star = \arg\max_{Q \in [Q_{\min}, Q_{\max}]} \mathrm{PnL}(Q)
\quad\text{subject to both legs fillable.}
\]

RFQ venue: evaluate only at configured reference notionals (no continuous search).

### 5.2 Shape (what we assume / do not assume)

- AMM impact is convex in size → marginal edge declines.
- Bybit book is a step function → \(\mathrm{PnL}(Q)\) can be **piecewise** with
  kinks at level boundaries; **not guaranteed unimodal**, not guaranteed concave.
- Therefore: **no pure ternary / gradient ascent alone** (same motivation as
  WHI-540 multi-peak search in the arbitrage-bots project).

### 5.3 Recommended algorithm (normative)

Constants (config; defaults below):

| Param | Default | Role |
|-------|---------|------|
| `q_min_usd` | `10` | Search floor (also smallest display bucket) |
| `q_max_usd` | `min(config_cap, depth_cap, amm_cap)` | See caps |
| `config_cap_usd` | `10000` | Global ceiling (top bucket) |
| `coarse_points` | `24` | Log-grid samples in \([Q_{\min}, Q_{\max}]\) |
| `refine_points` | `16` | Linear samples in each peak bracket |
| `peak_neighborhood` | 1 bracket on each side of coarse local max | |
| `q_tol_rel` | `1e-6` | Relative size merge / exact-out solve |

**Caps:**

```text
depth_cap  = max USD notional fillable on the Bybit side used by D
             (full walk of bids for BFSB sell / asks for BBSF buy)
amm_cap    = max Q such that V3 in-range math remains fillable
             (existing unfillable_or_range_exhausted)
Q_max      = min(config_cap_usd, depth_cap, amm_cap)
```

If \(Q_{\max} < Q_{\min}\): no fillable size → `optimal = null`.

**Procedure:**

1. **Coarse log grid:**  
   \(Q_i = \exp\bigl(\ln Q_{\min} + \frac{i}{n-1}(\ln Q_{\max}-\ln Q_{\min})\bigr)\),
   \(i=0..n-1\), \(n=\) `coarse_points`. Always include endpoints.
2. Evaluate \(\mathrm{PnL}(Q_i)\) (unfillable → \(-\infty\) / skip).
3. **Local peaks:** any \(i\) with \(\mathrm{PnL}(Q_i) \ge \mathrm{PnL}(Q_{i-1})\) and
   \(\ge \mathrm{PnL}(Q_{i+1})\) (endpoints: one-sided). Also retain the global
   coarse argmax if not already a peak (plateau guard).
4. **Refine:** for each peak, linearly sample `refine_points` in
   \([Q_{i-1}, Q_{i+1}]\) (clamp to domain).
5. **Endpoint check:** re-evaluate \(Q_{\min}\), \(Q_{\max}\).
6. **Winner:** max PnL among all evaluated fillable points. Ties → smaller \(Q\).

**Termination:** finite sample set; no iterative tolerance loop required beyond
AMM exact-out solve. Wall-clock budget: O(`coarse_points` + `peaks * refine_points`)
AMM+book evaluations — keep defaults so a full pair×direction pass stays
well under tens of ms on CPython for liquid books.

**Not claimed:** global continuous optimum. Claimed: best among the evaluated
set, which is dense enough for panel decisions at $10–$10k.

### 5.4 Fixed bucket table (WHI-756)

Normative buckets (USD):

```text
[10, 50, 100, 500, 1000, 10000]
```

For each bucket: `{pnl_usd, fillable, reason?, costs: {bybit_fee, bybit_slip, fluxion_fee, fluxion_slip, gas, basis}, q_base, ...}`.

---

## 6. Guardrails borrowed from Hummingbot

| Guard | Hummingbot | PnL v2 panel |
|-------|------------|--------------|
| Min profitability | Skip trade if `profit_pct < min` | Config `min_profit_bps` / `min_profit_usd` for **highlight / breach**; always compute raw PnL |
| Quote freshness | Implicit connector readiness | Require Bybit book age ≤ `bybit_stale_ms`; pool block ≤ `pool_stale_blocks`; RFQ quote age ≤ `rfq_stale_ms`. Stale → `fillable=false`, reason `stale_*` |
| Dual-leg time align | Same loop tick | Use same snapshot generation: max timestamp skew between Bybit tick and pool/RFQ ≤ `align_skew_ms` (default 2000). Else reason `skew` |
| Missing rate | `profit_pct = 0` + warning | Fail closed: `fillable=false`, reason `missing_quote` |
| Thin book | Partial volume in query result | Treat partial as unfillable (stricter than silent partial fill) |
| Fee double-count | Single fee_amount path | Cash-flow §4; wear breakdown is reporting-only |
| Stable basis | USD* interchangeable | Default β=0; document error: typically sub-5 bps, owned as DESIGN §8 open risk until measured |

Recommended config keys (WHI-756 may add under `config/metrics.yaml` or
`config/pnl_v2.yaml` — choice left to implementer, but names are reserved):

```yaml
pnl_v2:
  buckets_usd: [10, 50, 100, 500, 1000, 10000]
  search:
    q_min_usd: 10
    config_cap_usd: 10000
    coarse_points: 24
    refine_points: 16
  guards:
    bybit_stale_ms: 5000
    pool_stale_blocks: 3
    rfq_stale_ms: 30000
    align_skew_ms: 2000
    min_profit_bps: 0          # highlight only
  gas_on_rfq: true
  rfq_reference_usd: [100, 1000]
```

---

## 7. Worked micro-example (fee algebra check)

Assume mid-aligned venues, zero slip, zero gas, \(\beta=0\), \(f_b=10\) bps,
AMM fee already inside prices as 0 for clarity, \(Q=1000\), \(P=100\), \(q=10\).

**BFSB** with Bybit bid = Fluxion buy = 100:

```text
USDT_recv = 10 * 100 * (1 - 0.001) = 999.0
USDC_spent = 10 * 100 = 1000.0
PnL = 999 - 1000 = -1.0 USD   (= −10 bps of Q)
```

Matches “pay taker once on the CEX sell.” If Fluxion also charges 30 bps pool fee
on the buy input:

```text
USDC_spent ≈ 1000 / (1 - 0.003)   # exact-out style
           ≈ 1003.01
PnL ≈ 999 - 1003.01 = -4.01 USD
```

Hummingbot-style % of buy notional:

```text
profit_pct = (999 - 1003.01) / 1003.01 ≈ -0.40%
```

Our panel prefers **USD PnL** and **bps of \(Q\)** for operators; both are
derivable from the same cash-flows.

---

## 8. Implementation handoff (WHI-756)

| Deliverable | Notes |
|-------------|-------|
| Pure functions | `pnl_usd(...)`, `pnl_bucket_table(...)`, `optimal_size(...)` under `monitor/metrics/` (or `monitor/pnl/`) with no I/O |
| Inputs | De-multiplied L1 + optional depth, `AmmPoolState`, optional RFQ quote @ ref size, `MetricsConfig` / pnl_v2 config |
| Outputs | Serializable dataclasses for Web/TUI consumers; **do not** break `OverviewModel` |
| Tests | Fixed book + pool fixture → exact PnL per bucket; synthetic multi-peak book → search picks best sampled peak |
| DESIGN | §2.6 already normative; update only if implementation discovers a contradiction |

### 8.1 Explicit non-goals for the engine issue

- Placing orders or reading wallet balances.
- RFQ size continuum.
- Tick-crossing full V3 path beyond current in-range math (still mark unfillable).

---

## 9. Source index

| Topic | Path @ `2bfaccc` |
|-------|------------------|
| Proposal build | `hummingbot/strategy/amm_arb/utils.py` |
| `profit_pct` | `hummingbot/strategy/amm_arb/data_types.py` |
| Strategy loop / guards | `hummingbot/strategy/amm_arb/amm_arb.py` |
| VWAP walk | `hummingbot/core/data_type/order_book.pyx` (`c_get_vwap_for_volume`) |
| Fees | `hummingbot/core/data_type/trade_fee.py` |
| Budget | `hummingbot/connector/budget_checker.py` |
| v2 controller | `controllers/generic/arbitrage_controller.py` |
| v2 executor PnL | `hummingbot/strategy_v2/executors/arbitrage_executor/arbitrage_executor.py` |

Internal repo anchors: `src/monitor/metrics/edge.py`, `bybit_slip.py`,
`amm_slip.py`, `config/metrics.yaml`, `docs/DESIGN.md` §2.3 / §2.6.
