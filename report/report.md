# Mantle <> Bybit WMNT/USDT0 arbitrage: 29-day backtest

Window: 29.0 days ending 2026-07-28 13:24 UTC  
Venues: Agni V3 (PancakeSwap-V3 fork) and Merchant Moe Liquidity Book v2.2, vs Bybit spot MNTUSDT  
DEX quotes: 212,220 contract-quoted points across 16,688 state-change blocks (exact step function, not sampled)

## Bottom line

**Not as a standalone business, on this evidence.** A real dislocation does open -- but only the small rungs clear their inventory carry, and they clear it by amounts too small to pay for the operation.

- Profitable *only* up to **$10,000** per trade. Above that, execution cost outgrows the spread and every rung loses money against carry.
- The best rung *relative to carry* is $1,000: **$80 over 29 days** against $15.89 of carry, or 5.0x -- but only $2.76 per day in absolute terms. The largest absolute profit among rungs that clear carry at all is $200 at $10,000 ($6.91/day, 1.26x carry). Neither covers a developer, a server, or a monitoring rota.
- Only the $1,000 rung survives halving the profit to account for lost races, and 753 of 907 actionable windows were already contested.

The useful output of this study is therefore the *shape* of the opportunity -- where the size ceiling sits, which venue and direction carry it, and how briefly it lasts -- rather than a go-ahead. See **Inventory** below for the per-size arithmetic.

## Headline

**4,056 profitable windows** were found across all venue/direction/size combinations -- but only **907 (22.4%) lasted long enough to be taken** (>= 4s; see below). Read the count with that discount attached.

The most persistent combination was **agni_v3 direction A at $1,000**: profitable 0.216% of elapsed time across 583 windows, median duration 0.8s, p99 2.9m, longest 9.4m.

Estimated uncaptured profit for that combination: **$23** counting only windows a bot could reach, one round trip each, valued at window open. ($80 if every flicker were also counted; $280 if on top of that every window were timed to its in-window peak -- both are upper bounds, not forecasts.)

Of those 583 windows, **224 (38.4%) had at least one real on-chain swap land inside them** -- the rest went untouched. Matched on direction as well as venue and time: a window that says 'buy MNT on the DEX' is only counted as taken by a swap that actually bought MNT there.

## Why the floor is where it is

The hurdle is not the advertised 0.25% pool fee. Measured by quoting the real contracts:

| venue | one-way cost vs mid @ $1K | @ $10K | @ $100K |
|---|---|---|---|
| agni_v3 | 26.8 bps | 42.6 bps | 198.4 bps |
| moe_lb | 25.1 bps | 37.0 bps | 146.1 bps |

Add Bybit taker fee 10 bps, plus Bybit slippage from mid (which already includes crossing the half-spread, so it must not be added to it again). Bybit's own top-of-book is tight -- median spread 2.36 bps -- but its depth is finite, and that is what decides where the strategy stops scaling:

| size | Bybit slippage, selling | buying | cheapest DEX leg | total round-trip hurdle* |
|---|---|---|---|---|
| $1,000 | 1.26 bps | 1.26 bps | 25.1 bps | ~36 bps |
| $5,000 | 3.44 bps | 3.14 bps | 25.4 bps | ~39 bps |
| $10,000 | 5.29 bps | 3.45 bps | 37.0 bps | ~52 bps |
| $25,000 | 8.38 bps | 5.48 bps | 54.3 bps | ~73 bps |
| $50,000 | 14.32 bps | 10.34 bps | 85.0 bps | ~109 bps |
| $100,000 | 26.67 bps | 19.98 bps | 146.1 bps | ~183 bps |

\* cheapest venue's one-way DEX cost **at that same size** + taker fee + the worse of the two Bybit slippages. Indicative only -- the real per-row computation uses each block's own quote and each direction's own slippage, and never mixes sizes.

The composition shifts with size. At $1,000 the on-chain leg is 69% of the hurdle and Bybit depth is nearly free (1.3 bps); at $100,000 Bybit slippage alone (27 bps) exceeds the *entire* $1,000 hurdle of 36 bps, and the total is 5.0x larger. Both legs deteriorate with size, so the hurdle grows on both sides at once -- which is why a single headline size would have been misleading and the ladder is reported in full.

## Profitable-window duration

| venue | dir | size | windows | % of time | median | p95 | p99 | max | uncaptured $ | windows w/ a swap |
|---|---|---|---|---|---|---|---|---|---|---|
| agni_v3 | A | $1,000 | 583 | 0.2159% | 0.8s | 23.6s | 2.9m | 9.4m | $80 | 224/583 |
| agni_v3 | A | $5,000 | 222 | 0.0916% | 0.0s | 33.1s | 4.1m | 8.4m | $204 | 66/222 |
| agni_v3 | A | $10,000 | 184 | 0.0425% | 0.0s | 22.1s | 1.9m | 2.5m | $342 | 35/184 |
| agni_v3 | A | $25,000 | 24 | 0.0032% | 0.0s | 14.3s | 36.9s | 36.9s | $107 | 5/24 |
| agni_v3 | B | $1,000 | 631 | 0.1358% | 0.4s | 23.4s | 1.3m | 4.1m | $102 | 245/631 |
| agni_v3 | B | $5,000 | 282 | 0.0680% | 0.0s | 37.6s | 2.0m | 3.4m | $199 | 89/282 |
| agni_v3 | B | $10,000 | 179 | 0.0419% | 0.0s | 27.5s | 1.6m | 3.3m | $302 | 44/179 |
| agni_v3 | B | $25,000 | 82 | 0.0146% | 0.0s | 22.6s | 44.5s | 2.5m | $316 | 16/82 |
| agni_v3 | B | $50,000 | 11 | 0.0026% | 0.8s | 47.5s | 47.5s | 47.5s | $83 | 4/11 |
| moe_lb | A | $1,000 | 271 | 0.1965% | 0.4s | 1.6m | 5.9m | 10.4m | $45 | 87/271 |
| moe_lb | A | $5,000 | 197 | 0.1381% | 0.1s | 1.2m | 6.4m | 8.4m | $153 | 61/197 |
| moe_lb | A | $10,000 | 191 | 0.0796% | 0.0s | 29.5s | 3.4m | 8.3m | $306 | 58/191 |
| moe_lb | A | $25,000 | 50 | 0.0181% | 1.4s | 57.9s | 1.7m | 1.7m | $154 | 17/50 |
| moe_lb | A | $50,000 | 7 | 0.0001% | 0.0s | 1.8s | 1.8s | 1.8s | $11 | 1/7 |
| moe_lb | B | $1,000 | 466 | 0.1794% | 0.0s | 1.0m | 2.6m | 5.2m | $60 | 128/466 |
| moe_lb | B | $5,000 | 276 | 0.1260% | 0.1s | 1.2m | 2.8m | 5.2m | $197 | 85/276 |
| moe_lb | B | $10,000 | 250 | 0.0970% | 0.0s | 52.7s | 3.2m | 5.0m | $367 | 66/250 |
| moe_lb | B | $25,000 | 118 | 0.0467% | 0.0s | 39.7s | 2.7m | 4.1m | $450 | 29/118 |
| moe_lb | B | $50,000 | 31 | 0.0115% | 0.1s | 49.8s | 2.3m | 2.3m | $233 | 7/31 |
| moe_lb | B | $100,000 | 1 | 0.0001% | 2.5s | 2.5s | 2.5s | 2.5s | $21 | 1/1 |

Direction A = buy MNT on the DEX, sell on Bybit. Direction B = sell MNT on the DEX, buy back on Bybit.

### How many of those windows could actually be taken

A window is only an opportunity if a bot could see it and land a transaction inside it. Mantle blocks are 2s and inclusion in the very next block is not guaranteed, so **4s is the optimistic floor**. Below it the window is arithmetic, not opportunity. Splitting the table above on that floor:

| venue | dir | size | all windows | >= 4s | share | % of time (actionable) | uncaptured $ (actionable) |
|---|---|---|---|---|---|---|---|
| agni_v3 | A | $1,000 | 583 | 139 | 23.8% | 0.2001% | $23 |
| agni_v3 | A | $5,000 | 222 | 43 | 19.4% | 0.0877% | $39 |
| agni_v3 | A | $10,000 | 184 | 28 | 15.2% | 0.0403% | $54 |
| agni_v3 | A | $25,000 | 24 | 5 | 20.8% | 0.0029% | $32 |
| agni_v3 | B | $1,000 | 631 | 159 | 25.2% | 0.1222% | $30 |
| agni_v3 | B | $5,000 | 282 | 46 | 16.3% | 0.0630% | $36 |
| agni_v3 | B | $10,000 | 179 | 31 | 17.3% | 0.0400% | $34 |
| agni_v3 | B | $25,000 | 82 | 10 | 12.2% | 0.0136% | $49 |
| agni_v3 | B | $50,000 | 11 | 2 | 18.2% | 0.0023% | $14 |
| moe_lb | A | $1,000 | 271 | 83 | 30.6% | 0.1916% | $11 |
| moe_lb | A | $5,000 | 197 | 50 | 25.4% | 0.1344% | $29 |
| moe_lb | A | $10,000 | 191 | 39 | 20.4% | 0.0766% | $44 |
| moe_lb | A | $25,000 | 50 | 15 | 30.0% | 0.0169% | $46 |
| moe_lb | A | $50,000 | 7 | 0 | 0.0% | 0.0000% | $0 |
| moe_lb | B | $1,000 | 466 | 115 | 24.7% | 0.1731% | $16 |
| moe_lb | B | $5,000 | 276 | 62 | 22.5% | 0.1217% | $50 |
| moe_lb | B | $10,000 | 250 | 46 | 18.4% | 0.0937% | $69 |
| moe_lb | B | $25,000 | 118 | 27 | 22.9% | 0.0451% | $106 |
| moe_lb | B | $50,000 | 31 | 7 | 22.6% | 0.0111% | $24 |
| moe_lb | B | $100,000 | 1 | 0 | 0.0% | 0.0000% | $0 |

Across every combination, 907 of 4,056 windows (22.4%) clear the floor. The remainder are single-print flickers: real in the arithmetic, unreachable in practice.

Count and duration discount very differently, and conflating them is the easiest way to misread this table. Dropping the flickers removes 78% of the window COUNT but only 5% of profitable TIME -- the flickers are numerous and nearly durationless. Uncaptured profit follows the count, not the time, because each window is one round trip: $3,733 falls to **$706**. So the correct reading is that the spread is open about as *long* as the headline says, but can be *acted on* far fewer times than the window count implies.

## Inventory: the actual go/no-go

This is not a flash-loan strategy. Both legs must fire at once, so capital sits pre-positioned on both venues -- roughly 2x the trade size, since you hold both sides of the pair. That capital has a carrying cost whether or not a spread appears, so the only figure that decides anything is actionable uncaptured profit *at a given size* against carry *at that same size*. Comparing a $1K rung's profit to a $100K rung's carry, in either direction, is how this kind of study talks itself into a business that is not there.

| size | capital deployed | carry @ 10% APR over 29.0d | actionable uncaptured $ | verdict |
|---|---|---|---|---|
| $1,000 | $2,000 | $15.89 | $80 | clears carry 5.0x |
| $5,000 | $10,000 | $79.44 | $154 | marginal (1.94x carry) |
| $10,000 | $20,000 | $158.88 | $200 | marginal (1.26x carry) |
| $25,000 | $50,000 | $397.20 | $234 | **1.70x short of carry** |
| $50,000 | $100,000 | $794.40 | $38 | **21.02x short of carry** |
| $100,000 | $200,000 | $1,588.80 | $0 | **no profit at all** |

This sums both venues and both directions at each size, on the assumption that one pre-positioned book can serve all of them -- which is generous to the strategy, since it counts every combination against a single carrying cost.

**Two more reasons to read a passing verdict as a ceiling, not a forecast.** First, the uncaptured column assumes you win *every* actionable window; in fact 753 of 907 (83%) already had another party's swap land inside them, so those were races, not gifts. Halve every figure for a 50% win rate and only the $1,000 rung still clears it -- a verdict below 2x carry does not survive contact with competition. Second, this window is one particular 29 days of one particular pair; nothing here establishes that the spread recurs at the same rate next month.

## Method and its limits

- DEX prices come from quoting the live contracts (Agni QuoterV2, Merchant Moe `getSwapOut`) at every block where pool state changed. Between those blocks the state provably does not move, so the curve is exact rather than sampled.
- Because of that, event-driven sampling bias is avoided: we are not only looking at blocks where somebody traded (1.3% of blocks), which is precisely the subset that would hide 'a spread existed and nobody took it'.
- Bybit L1 is reconstructed from the public tape: taker-side `buy` prints mark the ask, `sell` prints the bid. This can only be wider than the true book, never tighter. RPI prints are excluded because that liquidity is not reachable by a bot.
- Venue alignment is strictly at-or-before: a DEX block at time T may only see Bybit prints published at or before T.
- **Main approximation:** Bybit depth beyond L1 comes from a single live orderbook snapshot, applied as a multiplicative slippage-vs-mid curve across the whole window. Historical L2 is not published. The book's *shape* is assumed stable; its absolute price is not.
- Gas is charged per swap from each block's own base fee plus the OP-Stack L1 data fee, and is a rounding error (~0.05 bps at $1K).
- **Time resolution is coarser than the actionable floor, and this is the weakest part of the duration measurement.** Profitability is evaluated at Bybit print times. Prints are bursty: the median gap is 4ms, but weighted by the time it actually covers the mean gap is 12s, because quiet stretches dominate the clock. So a duration near the 4s floor carries granularity error of that order, in both directions: some sub-floor flickers are really longer windows seen once, and some multi-second windows are a single quiet-period gap. Treat the >= 4s split as a bucket, not a measurement.
- Relatedly, 2% of elapsed time sits in DEX intervals that no print falls inside, so they are never evaluated at all. They are short by construction (rapid-fire state changes that resolve before the next print) and therefore below the actionable floor anyway, but they are a true gap in coverage rather than a measured zero.
- Out of scope by design: order placement, multi-hop/triangular routes, MEV and gas-auction dynamics, realtime operation.

