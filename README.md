# Mantle ⇄ Bybit arbitrage feasibility backtest (WMNT/USDT0)

A POC that answers one question with numbers rather than intuition:

> Over the past ~29 days, did a profitable arbitrage window exist between
> WMNT/USDT0 on Mantle's two main DEXes and Bybit spot MNTUSDT — how often, and
> **for how long**?

Duration is the first-class metric. "A spread existed" is nearly meaningless on
its own: a 2-second window is noise no bot can act on after block inclusion,
while a 20-minute window is a standing invitation.

**This tool places no orders and executes no trades.** It needs no exchange API
keys — Bybit data comes from public endpoints and public CSV archives.

## Setup

```bash
uv sync
cp .env.example .env      # then paste your keyed Mantle endpoint into it
```

`MANTLE_RPC_URL` is optional. Without it the code falls back to the public
`https://rpc.mantle.xyz`, which caps `eth_getLogs` at 10k blocks and throttles
hard — expect the scan stages to take substantially longer.

## Running the pipeline

The six stages are ordered and each reads the previous one's Parquet output, so
run them in sequence. Use `python -u`; without it the progress lines sit in
stdout's buffer and a redirected log looks frozen.

```bash
uv run python -u -m mba.m1_scan_events    # scan Swap/Mint/Burn logs
uv run python -u -m mba.m2_quotes         # quote both pools at every such block
uv run python -u -m mba.m3_bybit          # Bybit tape + live book
uv run python -u -m mba.m6_attribution    # who traded, what they made
uv run python -u -m mba.m4_align          # align venues, net profit
uv run python -u -m mba.m5_report         # windows, report, charts
```

**M6 before M5, despite the numbering.** M6 only needs M1 and M3, and its decoded
swap directions let M5's did-anyone-take-it check match on direction — a window
that says "buy MNT on the DEX" should only count as taken by a swap that actually
bought MNT there. Run M5 first and it still works, but that check falls back to
direction-blind and reports itself as an upper bound.

M2 dominates the wall clock: it makes two passes (read state, then quote) over
~17,700 state-change blocks across both pools, and on the keyed endpoint it
sustains 8–14 blocks/s, so **budget about an hour**. M1 and M3 are minutes. M4
(~0.5s) and M5 (~4s) touch only local Parquet; M6 is also seconds, but does make
a small bounded number of RPC calls — one batched `eth_getCode` sweep to tell
contracts from EOAs, plus a handful of transaction fetches to classify the top
beneficiaries. Throughput is endpoint-bound, so on the public RPC everything
upstream of M4 takes considerably longer.

M1 and M2 both **resume**: re-running after an interruption picks up from the last
completed partial rather than starting over. M2 keeps per-pool, per-pass partials
in `data/m2_{state,quotes}_<pool>.parquet`.

Useful flags:

| flag | stage | effect |
|---|---|---|
| `--days N` | M1, M2, M3 | shorten the backfill window |
| `--limit N` | M2 | quote only the first N state-change blocks (smoke test) |
| `--no-resume` | M1 | discard partials and rescan |
| `--max-lag-ms N` | M4 | staleness threshold for a matched Bybit print |
| `--top-n N` | M6 | how many beneficiaries to break out |

## Outputs

```
report/report.md         the answer: window counts, durations, go/no-go vs carry
report/attribution.md    who actually traded these pools, and what they made
report/*.png             cost vs size, duration CDF, % time profitable, P&L series
data/*.parquet           every intermediate, so any figure can be re-derived
```

## What the stages do

| stage | output | what it establishes |
|---|---|---|
| **M1** | `raw_logs`, `state_changes` | every block where pool state changed |
| **M2** | `dex_quotes` | contract-quoted price at each of those blocks, per size, both directions |
| **M3** | `bybit_quotes`, `bybit_book` | L1 reconstructed from the public tape + one live L2 snapshot for depth |
| **M6** | `swaps_decoded` | concentration, contract-vs-EOA, clustering, realized P&L |
| **M4** | `opportunities`, `intervals` | venues aligned at-or-before; net profit and breakeven Bybit mid |
| **M5** | `windows`, `window_summary` | profitable windows, their durations, and the verdict against carry |

## Why the method is what it is

**Pool state is piecewise-constant.** It only moves on Swap/Mint/Burn (Agni) and
Swap/DepositedToBins/WithdrawnFromBins (Moe). Quoting at exactly those blocks
yields an **exact step function**, not a sample — a long gap between quotes means
the price genuinely did not move, not that we looked away.

**That is also what avoids the fatal sampling bias.** Sampling only blocks where
somebody traded covers ~1.3% of blocks, and it is precisely the subset that
*hides* the finding of interest: "a spread existed and nobody took it."

**Profitability factors into a step-function comparison.** Over one DEX interval
the quantity and USD leg are fixed, so net profit is affine in the Bybit mid with
constant slope. Profitable ⇔ mid on the right side of a constant `breakeven_mid`.
That turns duration measurement into an exact comparison instead of a 24-way
cross join of millions of prints. It is the same arithmetic, factored — not an
approximation.

**Agni is a PancakeSwap-V3 fork, not vanilla Uniswap V3.** Its `Swap` event
carries two extra `protocolFees` arguments, so the Uniswap `topic0` matches
nothing. Getting this wrong yields a silent zero-row scan.

## Known limits

- **Main approximation:** Bybit depth beyond L1 comes from a single live
  orderbook snapshot applied as a multiplicative slippage-vs-mid curve across the
  window. Historical L2 is not published. The book's *shape* is assumed stable;
  its absolute price is not.
- Bybit slippage is measured *from mid* and therefore already includes crossing
  the half-spread. Adding it to a bid/ask price double-counts.
- **Window durations resolve to Bybit print times, and that resolution is
  coarser than the 4s actionable floor** — the weakest part of the duration
  measurement. Prints are bursty: median gap 4ms, but weighted by the time it
  actually covers the mean gap is 12s, because quiet stretches dominate the
  clock. A duration near the floor therefore carries granularity error of that
  order in *both* directions. Separately, 1.9% of elapsed time sits in DEX
  intervals no print falls inside, so it is never evaluated at all.
- Mantle archive state reaches only ~30 days on both endpoints, which is what
  fixes the window at 29 days.
- The RPC is load-balanced and **not** read-your-writes consistent, so all
  queries run behind `HEAD_LAG_BLOCKS` of the reported head.

## Out of scope by design

No order placement or trade execution. No triangular or multi-hop routing. No
MEV, gas-auction, or frontrunning modeling. No realtime pipeline. No venues
beyond Agni V3 and Merchant Moe LB. No Bybit private API.
