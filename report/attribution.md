# Attribution: who traded these pools, and what did they make

_Computed from decoded on-chain Swap events plus `eth_getCode`, not from a third-party index. Realized profit is what closing the opposite leg on Bybit at that instant would have paid, net of Bybit taker fee and exact-VWAP slippage for that swap's own notional._

**13,780 swaps**, $10,984,035 total notional, 100 distinct beneficiaries.

## Concentration

### agni_v3

10,233 swaps, $6,351,022 notional, 56 beneficiaries. Top-10 share **97.9%**, HHI **0.255** (highly concentrated).

| # | beneficiary | contract | role | swaps | notional $ | realized $ | bps |
|---|---|---|---|---|---|---|---|
| 1 | `0x0000004e…1bb8f1` | no | internal | 1,642 | $1,942,425 | $17 | 0.1 |
| 2 | `0x8fc13082…76fabb` | yes | entrypoint | 1,680 | $1,790,960 | $-604 | -3.4 |
| 3 | `0xd87a4c48…e33f1f` | yes | entrypoint | 1,811 | $1,780,773 | $-1,720 | -9.7 |
| 4 | `0x1c96d7fa…ddf0a0` | no | internal | 821 | $348,731 | $-390 | -11.2 |
| 5 | `0xc1ed7ed1…ceff26` | no | internal | 206 | $102,386 | $145 | 14.2 |
| 6 | `0xcf769841…b7ecf4` | yes | entrypoint | 948 | $63,051 | $82 | 13.0 |
| 7 | `0x6e3d8943…df0b4b` | no | internal | 41 | $59,787 | $-28 | -4.7 |
| 8 | `0x319b6988…c88421` | yes | entrypoint | 45 | $55,900 | $-80 | -14.3 |
| 9 | `0xe3fbe788…3d8d2d` | yes | internal | 84 | $38,980 | $-184 | -47.1 |
| 10 | `0x0d05a7d3…4a0d05` | yes | internal | 12 | $36,653 | $-207 | -56.6 |

### moe_lb

3,547 swaps, $4,633,012 notional, 61 beneficiaries. Top-10 share **99.0%**, HHI **0.529** (highly concentrated).

| # | beneficiary | contract | role | swaps | notional $ | realized $ | bps |
|---|---|---|---|---|---|---|---|
| 1 | `0x890e3754…fbb1e8` | yes | entrypoint | 1,748 | $3,256,516 | $1,396 | 4.3 |
| 2 | `0xd87a4c48…e33f1f` | yes | entrypoint | 413 | $834,835 | $-172 | -2.1 |
| 3 | `0x9c69324f…5fe27b` | no | internal | 138 | $122,914 | $-104 | -8.5 |
| 4 | `0xcf769841…b7ecf4` | yes | entrypoint | 367 | $119,041 | $280 | 23.5 |
| 5 | `0xc1ed7ed1…ceff26` | no | internal | 328 | $115,987 | $230 | 19.8 |
| 6 | `0x32073633…8b7910` | no | internal | 7 | $39,133 | $-219 | -56.0 |
| 7 | `0xe3fbe788…3d8d2d` | yes | internal | 56 | $36,291 | $-147 | -40.4 |
| 8 | `0x4e2abccf…63ff20` | yes | internal | 92 | $32,587 | $-147 | -45.2 |
| 9 | `0x28104d4f…f33f95` | yes | internal | 28 | $21,068 | $92 | 43.6 |
| 10 | `0xf7962a83…3d1789` | yes | internal | 11 | $9,985 | $91 | 91.4 |

**Read the concentration numbers with this caveat:** the beneficiary is the address the pool paid, which for a router or aggregator is the router itself, not the person behind it. Addresses marked `entrypoint` above are called directly by users (verified: `tx.to` equals the beneficiary), so the volume behind them is an unknown number of distinct traders. Concentration is therefore measured at the EXECUTOR level. For the arbitrage question that is arguably the level that matters -- the executor is the bot -- but it is an upper bound on true trader concentration, not an estimate of it.

## Contract vs EOA

| beneficiary | addrs | swaps | notional $ | realized $ |
|---|---|---|---|---|
| contract | 54 | 10,513 | $8,236,040 | $-1,713 |
| EOA | 46 | 3,267 | $2,747,995 | $-392 |

## Realized profit vs Bybit

Of 13,780 priced swaps, **3,028 (22.0%)** would have been profitable had the trader simultaneously closed on Bybit.

- Sum over profitable swaps: **$4,273** (median 14.6 bps)
- Sum over ALL priced swaps: **$-2,105** (median -7.7 bps)

The second figure is the honest one for 'is this flow arbitrage': if the population of takers were arbitrageurs, the all-swaps total would be solidly positive. A negative total means most on-chain flow is paying the spread, not harvesting it -- i.e. it is end-user demand, and the arbitrage is being done by a small subset.

| venue | dir | swaps | notional $ | realized $ | median bps | % profitable |
|---|---|---|---|---|---|---|
| agni_v3 | A | 4,855 | $3,087,042 | $-1,531 | -8.3 | 7.9% |
| agni_v3 | B | 5,378 | $3,263,980 | $-1,773 | -11.2 | 9.1% |
| moe_lb | A | 1,531 | $2,246,734 | $446 | 1.5 | 58.8% |
| moe_lb | B | 2,016 | $2,386,278 | $754 | 3.4 | 62.5% |

**Calibration.** The largest self-directed executor -- excluding routers, whose volume is aggregated end-user flow -- is `0x0000004eba872864a71b957180eb17dff71bb8f1` (EOA), which pushed $1,942,425 of notional for $17 net, **+0.1 bps**.

A high-volume, evidently automated participant landing this close to zero is the strongest available evidence that this cost model is calibrated rather than merely plausible. Were it materially too harsh or too generous, the most active professional flow on the venue would not sit at its breakeven.

The split by venue is the substantive result:

- **agni_v3**: 8% of swaps profitable, median -9.6 bps, total $-3,305
- **moe_lb**: 61% of swaps profitable, median +2.5 bps, total $1,200

The two venues are not competing on equal terms, and the reason is structural rather than incidental. Measured on real swaps against each pool's own **pre-swap** mid, at matched notional:

| notional | agni_v3 med cost (n) | moe_lb med cost (n) | gap | share of Agni flow |
|---|---|---|---|---|
| < $500 | 25.1 bps (6,021) | 18.8 bps (1,971) | 6.3 bps | 59% |
| $500 - $2K | 26.5 bps (3,608) | 18.9 bps (868) | 7.7 bps | 35% |
| $2K - $10K | 29.6 bps (594) | 18.9 bps (649) | 10.7 bps | 6% |
| > $10K † | 43.8 bps (9) | 43.9 bps (57) | 0.1 bps | 0% |

† fewer than 30 swaps on one side of the comparison. Reported for completeness; the median is not a reliable estimate at that count and the gap in that row should not be read as a trend.

Agni's cost has a hard floor at its 25 bps fee tier and then rises with price impact. Merchant Moe's Liquidity Book charges a flat ~18.8 bps for any swap that fits inside the active bin, with no impact at all -- so the gap does not merely exist, it *widens* with size, across every bucket populated enough to measure. Routing to Moe removes 6-11 bps -- 17%-29% of the ~36 bps round-trip hurdle at $1K, which is what lets the same dislocation clear Moe's bar and not Agni's. Any live strategy should route to Moe and treat Agni as a venue of last resort.

**This does not contradict the cost ladder in `report.md`,** which shows the two venues within ~2 bps of each other at a fixed $1,000. That table quotes $1,000 at *every* state-change block, including moments when Moe's active bin is nearly exhausted and the order must cross bins at 25 bps apiece. Real traders do not route then. The ladder is the cost of demanding liquidity at an arbitrary instant; this table is the cost actually paid by flow that chose its moment. The difference between them is the value of that choice.

## Temporal clustering

- **agni_v3**: median gap between swaps 18s, p10 2.0s, p90 588s. 15.1% of swaps land within 2 blocks of the previous one.
- **moe_lb**: median gap between swaps 8s, p10 0.0s, p90 2128s. 37.1% of swaps land within 2 blocks of the previous one.

Bybit quote staleness at swap time: median 1291 ms, p95 7795 ms.

