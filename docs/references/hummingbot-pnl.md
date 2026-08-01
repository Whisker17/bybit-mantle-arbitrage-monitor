# Hummingbot CEX⇄AMM PnL methodology → PnL v2 spec (WHI-754)

Research note for **PnL v2**: extract Hummingbot’s arbitrage profitability math,
map it onto Bybit ⇄ Fluxion xStocks, and pin formulas implementable by WHI-756
without re-opening product decisions.

**Normative product section:** `docs/DESIGN.md` §2.6 (PnL v2). This file is the
derivation and Hummingbot comparison; when they disagree, DESIGN wins for
product, and this note must be patched.

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

**v1 vs v2 accounting comparison:**

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
| \(D\) | enum | `buy_fluxion_sell_bybit` or `buy_bybit_sell_fluxion` (M3 names) |
| \(V\) | enum | Venue on Fluxion: `amm` or `rfq` |

**Sign convention:** \(\mathrm{PnL} > 0\) means paper profit in USD after fees and gas.

**No truncation:** if gas or fees dominate, \(\mathrm{PnL}\) is **negative** and reported as-is (WHI-756).

### 4.2 Bybit leg — VWAP + taker fee

Inputs: ordered book side levels \(\{(p_i, s_i)\}\) with **de-multiplied** price
\(p_i\) (USDT per base) and size \(s_i\) in **base** units consistent with \(q\).

**Walk base amount** (Hummingbot `get_vwap_for_volume` style — sized in **base**,
not USD):

```text
remaining = q_walk
cost_or_proceeds = 0
for (p, s) in levels:           # asks if buying, bids if selling
    take = min(s, remaining)
    cost_or_proceeds += take * p
    remaining -= take
    if remaining <= 0: break
if remaining > 0: UNFILLABLE
P_vwap = cost_or_proceeds / q_walk
```

M3 `book_vwap_slip_bps` walks by **USD notional** instead (`spent` until
`size_usd`). That yields a different base fill \(Q / P_{\mathrm{vwap}}\) rather
than \(Q / P_b^{\mathrm{mid}}\). **PnL v2 normative walk is base-sized** (this
section). An engine may adapt the M3 helper by converting \(q \mapsto\) notional
with care, but must not claim the two walks are identical.

#### Fee currency (Bybit spot, received-asset rule)

Bybit spot charges the taker fee in the **received** coin (official Spot Fees
examples: buy BTC → fee in BTC; sell BTC → fee in USDT). With rate \(f_b\):

| Leg | Gross trade | Net cash / inventory |
|-----|-------------|----------------------|
| **Buy** base (want **net** base \(q\)) | Walk asks for gross base \(q_{\mathrm{g}} = q / (1 - f_b)\); pay \(q_{\mathrm{g}} \cdot P_{\mathrm{ask}}^{\mathrm{vwap}}\) USDT | Receive \(q\) base after fee |
| **Sell** base \(q\) | Walk bids for base \(q\); gross USDT \(= q \cdot P_{\mathrm{bid}}^{\mathrm{vwap}}\) | Receive \(q \cdot P_{\mathrm{bid}}^{\mathrm{vwap}} \cdot (1 - f_b)\) USDT |

First-order, \(1/(1-f_b) \approx 1+f_b\) (error \(\sim f_b^2\), ~0.01 bps at
10 bps). **Implement the exact received-asset form** so the matched-base
invariant stays honest; do not bill buy fees as pure quote markup without
adjusting gross base.

Notes:

1. Fee is **not** double-counted with half-spread: VWAP already sits on the
   trade side of mid; fee is separate.
2. Without depth, degrade to L1: \(P_{\mathrm{ask}}^{\mathrm{vwap}} = \mathrm{ask}_1\),
   \(P_{\mathrm{bid}}^{\mathrm{vwap}} = \mathrm{bid}_1\) (M3 today). Mark
   `bybit_depth_source = l1` vs `book`.
3. **Multiplier step:** de-multiply **before** VWAP. Never de-multiply VWAP
   after walking raw levels with native sizes (that would mismatch \(q\)).

### 4.3 Fluxion leg — AMM vs RFQ

#### 4.3.1 AMM (V3)

Repo reality (`monitor.metrics.amm_slip`): **exact-in only**
(`swap_exact_in_zero_for_one` / `swap_exact_in_one_for_zero`). There is no
on-path exact-out helper today.

**Normative sizing for AMM buckets:**

1. Fix \(q = Q / P_b^{\mathrm{mid}}\) (target net base on both legs).
2. **Sell base on Fluxion:** exact-in base \(q\) → `USDC_recv` (fee on input is
   inside the UniV3 formula when `pool_fee` is applied; for cash-flow use
   fee-inclusive swap — see below).
3. **Buy base on Fluxion:** binary-search quote_in (USDC) such that exact-in
   base_out equals \(q\) within relative tolerance `q_tol_rel` (default
   **`1e-6`**). Cap iterations at `amm_solve_max_iters` (default **64**).
   Fail → `fillable=false`, reason `amm_size_solve_failed` or
   `unfillable_or_range_exhausted`.

Optional future: true exact-out math or Quoter contract; until then the
binary search **is** the implementable path, not a fallback.

Pool fee vs impact in the UI: wear breakdown may split lines, but
\(\mathrm{PnL}\) uses **one** fee-inclusive cash amount (do not subtract pool
fee twice). Gas: subtract \(G\) USD once per Fluxion leg.

#### 4.3.2 RFQ (poll-native rows — not the AMM bucket ladder)

RFQ is EXACT_INPUT only (`docs/references/m1-rfq-feasibility.md`). Collector
today (`config/collector.yaml`):

| Side | Poll input | Typical config |
|------|------------|----------------|
| Buy base (USDC → native) | USDC raw amount | `amount_usdc_raw` ≈ 100 USDC |
| Sell base (native → USDC) | Native base raw amount | `amount_native_raw` ≈ 0.1 token |

**Relaxation of the AMM same-\(Q\) grid:**

- RFQ PnL rows are **keyed by the poll**, not by the $10…$10k AMM buckets.
- **Buy Fluxion / sell Bybit:** `USDC_spent = amountIn` from the poll;
  \(q = \mathrm{amountOut}\) (base). Bybit sell walks that **same** \(q\).
  Label row with `rfq_input_usdc` and display notional \(Q_{\mathrm{label}} = q \cdot P_b^{\mathrm{mid}}\) if needed.
- **Buy Bybit / sell Fluxion:** poll sells base `amount_native`;
  \(q = \mathrm{amountIn}\) (base). Bybit buy acquires **net** \(q\) after fee
  (§4.2). `USDC_recv = amountOut`.
- No RFQ impact curve for off-poll sizes. Do not invent $1k RFQ from a $100
  poll. Expanding polls is a **collector** change outside WHI-754; WHI-756
  consumes whatever polls exist.
- Executable price is the poll response; **no extra pool fee line**.
- Gas: charge \(G\) by default (on-chain LOP settlement).

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

All names below are from the **trader** perspective: `*_spent` leaves the wallet,
`*_recv` enters it.

#### Direction `buy_fluxion_sell_bybit` (AMM)

```text
q           = Q / P_b_mid                         # net base
USDC_spent  = amm_quote_in_for_base_out(q)        # binary-search exact-in; fee-inclusive
USDT_recv   = q * P_bid_vwap * (1 - f_b)          # sell net q; fee in quote
PnL_USD     = usd(USDT_recv) - usd(USDC_spent) - G
```

#### Direction `buy_bybit_sell_fluxion` (AMM)

```text
q           = Q / P_b_mid                         # net base
q_gross     = q / (1 - f_b)                       # Bybit buy fee in base
USDT_spent  = q_gross * P_ask_vwap                # walk asks for q_gross
USDC_recv   = amm_quote_out_for_base_in(q)        # exact-in base q; fee-inclusive
PnL_USD     = usd(USDC_recv) - usd(USDT_spent) - G
```

#### RFQ variants (poll-keyed)

```text
# buy_fluxion_sell_bybit @ RFQ buy poll
USDC_spent = rfq.amountIn
q          = rfq.amountOut
USDT_recv  = q * P_bid_vwap * (1 - f_b)
PnL_USD    = usd(USDT_recv) - usd(USDC_spent) - G

# buy_bybit_sell_fluxion @ RFQ sell poll
q          = rfq.amountIn          # base sold on Fluxion
USDC_recv  = rfq.amountOut
q_gross    = q / (1 - f_b)
USDT_spent = q_gross * P_ask_vwap
PnL_USD    = usd(USDC_recv) - usd(USDT_spent) - G
```

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
- Therefore: **no pure ternary / gradient ascent alone**. (Precedent: multi-peak
  optimal-input search in the separate *Mantle Arbitrage bots v2* project —
  Linear WHI-540 there — same “don’t assume concavity” motivation.)

### 5.3 Recommended algorithm (normative)

Defaults (WHI-756 may put these under `config/metrics.yaml` or a sibling file;
values below are the research defaults, not a reserved schema):

| Param | Default | Role |
|-------|---------|------|
| `q_min_usd` | `10` | Search floor (also smallest AMM display bucket) |
| `config_cap_usd` | `10000` | Global ceiling (top AMM bucket) |
| `coarse_points` | `24` | Log-grid samples in \([Q_{\min}, Q_{\max}]\) |
| `refine_points` | `16` | Linear samples in each peak bracket |
| `q_tol_rel` | `1e-6` | AMM buy size-solve relative tolerance (§4.3.1) |
| `amm_solve_max_iters` | `64` | Cap binary-search iterations |

**Caps:**

```text
depth_cap  = max USD notional fillable on the Bybit side used by D
             (full walk of bids for sell-Bybit / asks for buy-Bybit,
              using the gross base when the Bybit leg is a buy)
amm_cap    = max Q such that V3 in-range math remains fillable
             (existing unfillable_or_range_exhausted)
Q_max      = min(config_cap_usd, depth_cap, amm_cap)
```

If \(Q_{\max} < Q_{\min}\): no fillable size → `optimal = null`.

**Procedure:**

1. **Coarse log grid:**  
   \(Q_i = \exp\bigl(\ln Q_{\min} + \frac{i}{n-1}(\ln Q_{\max}-\ln Q_{\min})\bigr)\),
   \(i=0..n-1\), \(n=\) `coarse_points`. Always include endpoints.
2. Evaluate \(\mathrm{PnL}(Q_i)\) (unfillable → skip).
3. **Local peaks:** any \(i\) with \(\mathrm{PnL}(Q_i) \ge \mathrm{PnL}(Q_{i-1})\) and
   \(\ge \mathrm{PnL}(Q_{i+1})\) (endpoints: one-sided). Also retain the global
   coarse argmax if not already a peak (plateau guard).
4. **Refine:** for each peak, linearly sample `refine_points` in
   \([Q_{i-1}, Q_{i+1}]\) (clamp to domain).
5. **Endpoint check:** re-evaluate \(Q_{\min}\), \(Q_{\max}\).
6. **Winner:** max PnL among all evaluated fillable points. Ties → smaller \(Q\).

**Termination:**

- Outer search: finite sample set (no open-ended optimizer).
- Inner AMM buy solve: stop when relative base error \(\le\) `q_tol_rel` or
  iterations hit `amm_solve_max_iters` (then unfillable).

Wall-clock: O(`coarse_points` + `peaks * refine_points`) AMM+book evaluations —
defaults should stay comfortable for live snapshot use on liquid books.

**Not claimed:** global continuous optimum. Claimed: best among the evaluated
set, which is dense enough for panel decisions at $10–$10k.

### 5.4 Fixed bucket table (WHI-756) — AMM path only

Normative AMM buckets (USD) — **not** the M3 TUI ladder ($1k/$5k/$20k; DESIGN
§2.6.3):

```text
[10, 50, 100, 500, 1000, 10000]
```

For each bucket: `{pnl_usd, fillable, reason?, costs: {bybit_fee, bybit_slip, fluxion_fee, fluxion_slip, gas, basis}, q_base, ...}`.

RFQ: separate poll-keyed rows (§4.3.2), not this list.

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

Suggested knobs for WHI-756 (illustrative — not a frozen schema; implementer
chooses file layout and exact names):

- AMM `buckets_usd`, search defaults from §5.3
- Freshness / align thresholds from the guard table above
- `min_profit_bps` (highlight only), `gas_on_rfq` (default true)
- RFQ rows follow **collector poll config**, not a second hard-coded USD list

---

## 7. Worked micro-example (fee algebra check)

Assume mid-aligned venues, zero slip, zero gas, \(\beta=0\), \(f_b=10\) bps,
AMM fee 0 for clarity, \(Q=1000\), \(P=100\), net \(q=10\).

**`buy_fluxion_sell_bybit`** with Bybit bid = Fluxion mid = 100:

```text
USDT_recv  = 10 * 100 * (1 - 0.001) = 999.0   # fee in quote on sell
USDC_spent = 10 * 100 = 1000.0                # fee-free AMM buy of net 10
PnL = 999 - 1000 = -1.0 USD   (= −10 bps of Q)
```

Matches “pay taker once on the CEX sell.” If the UniV3 pool fee is 30 bps on
input and we binary-search quote_in for base_out = 10:

```text
USDC_spent ≈ 1000 / (1 - 0.003) ≈ 1003.01
PnL ≈ 999 - 1003.01 = -4.01 USD
```

**`buy_bybit_sell_fluxion`** same mids:

```text
q_gross    = 10 / (1 - 0.001) ≈ 10.01001      # fee in base on buy
USDT_spent = 10.01001 * 100 ≈ 1001.001
USDC_recv  = 10 * 100 = 1000.0
PnL ≈ 1000 - 1001.001 = -1.001 USD
```

Hummingbot-style % of buy notional remains available from the same cash-flows;
the panel prefers **USD PnL** and optional `pnl_bps = PnL/Q * 1e4`.

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
