# MM attribution analysis (WHI-767)

Research note: per-address inventory ledger + draft `market_maker` / `rebalancer` rules for Fluxion xStocks, with evidence from a full-history chain backfill (collector journal alone is too short / weekend-sparse).

**Generated:** 2026-08-02 04:47 UTC

## Method

| Item | Value |
|------|--------|
| Window | 30 days ending block `98757762` |
| From block | `97461762` |
| Pools (AMM) | 8 liquid inventory pairs |
| AMM swaps decoded | 6441 |
| LOP OrderFilled | 18 |
| ERC-20 Transfer (native+wrapper) | 8030 |
| Distinct addresses (ledger) | 112 |
| Bybit mids | public 1m klines, de-multiplied by `pairs.yaml` xstockMultiplier |
| Session | `monitor.metrics.session.session_kind` (NYSE calendar) |
| Repro | `uv run python scripts/mm_attribution_analysis.py` (cache under `data/mm_analysis/`) |
| Pure rules | `monitor.attribution.mm_draft` (unit-tested; productize in WHI-768) |

### Inventory sign convention

- **AMM:** taker = Swap `recipient` (M4). `delta_native` ≈ −Δwrapper_pool (buy_native → positive inventory).
- **RFQ:** maker/taker from receipt Transfer/Approval graph (see § RFQ decode). Maker providing stock → `sell_native`.
- **Transfer:** `to` +amount, `from` −amount (native and wrapper both map to the same `pair_id` — double-count risk noted in data gaps).

## Label counts (draft priority order)

| Label | Addresses |
|-------|----------:|
| `market_maker` | 5 |
| `rebalancer` | 15 |
| `arb_bot` | 3 |
| `price_keeper` | 0 |
| `retail` | 3 |
| `unknown` | 86 |

## Candidate addresses (evidence)

Addresses are lowercased. Explore on [Mantlescan](https://mantlescan.xyz/address/). Tx evidence links use the same explorer.

### Market makers (`market_maker`)

| Address | Contract? | Pairs | AMM | RFQ m/t | Conv | Mean-rev | CEX touches | Why |
|---------|-----------|------:|----:|--------:|-----:|---------:|------------:|-----|
| [`0x1baab0b2…`](https://mantlescan.xyz/address/0x1baab0b2e787eff5b3753addf31a1bcdc2417f9b) | no | 2 (SPCXx,UNKNOWN) | 0 | 7/0 | — | 0.43 | 20 | rfq_maker_fills=7>=2 |
| [`0x804b1024…`](https://mantlescan.xyz/address/0x804b10246b7d495517411b788f46134e731779cb) | no | 2 (SPCXx,UNKNOWN) | 0 | 3/0 | — | 0.00 | 0 | rfq_maker_fills=3>=2 |
| [`0x2b90c1dc…`](https://mantlescan.xyz/address/0x2b90c1dcfc676be2eb2ffaac1b37ac5f5c04662e) | no | 2 (METAx,UNKNOWN) | 0 | 2/0 | — | 0.50 | 0 | rfq_maker_fills=2>=2 |
| [`0xb4a4a76e…`](https://mantlescan.xyz/address/0xb4a4a76e99e2acc1e51567c0231628780dfab0c8) | no | 2 (SPCXx,UNKNOWN) | 0 | 2/0 | — | 0.25 | 0 | rfq_maker_fills=2>=2 |
| [`0xd8949655…`](https://mantlescan.xyz/address/0xd894965581a598c0b9df5babaca73e714359e8b6) | no | 1 (UNKNOWN) | 0 | 2/0 | — | 0.00 | 0 | rfq_maker_fills=2>=2 |

#### `0x1baab0b2e787eff5b3753addf31a1bcdc2417f9b`

- Label: **market_maker**
- Reasons: rfq_maker_fills=7>=2
- Activity: AMM=0 buy=5 sell=2 RFQ maker=7 taker=0 transfers=60
- Notional (sum USD proxy): 3274.599135 (median 350)
- Explorer: https://mantlescan.xyz/address/0x1baab0b2e787eff5b3753addf31a1bcdc2417f9b

#### `0x804b10246b7d495517411b788f46134e731779cb`

- Label: **market_maker**
- Reasons: rfq_maker_fills=3>=2
- Activity: AMM=0 buy=2 sell=1 RFQ maker=3 taker=0 transfers=1
- Notional (sum USD proxy): 127.42847 (median 30)
- Explorer: https://mantlescan.xyz/address/0x804b10246b7d495517411b788f46134e731779cb

#### `0x2b90c1dcfc676be2eb2ffaac1b37ac5f5c04662e`

- Label: **market_maker**
- Reasons: rfq_maker_fills=2>=2
- Activity: AMM=0 buy=1 sell=1 RFQ maker=2 taker=0 transfers=5
- Notional (sum USD proxy): 21.999766 (median 10.999883)
- Explorer: https://mantlescan.xyz/address/0x2b90c1dcfc676be2eb2ffaac1b37ac5f5c04662e

#### `0xb4a4a76e99e2acc1e51567c0231628780dfab0c8`

- Label: **market_maker**
- Reasons: rfq_maker_fills=2>=2
- Activity: AMM=0 buy=2 sell=0 RFQ maker=2 taker=0 transfers=13
- Notional (sum USD proxy): 920.634794 (median 460.317397)
- Explorer: https://mantlescan.xyz/address/0xb4a4a76e99e2acc1e51567c0231628780dfab0c8

#### `0xd894965581a598c0b9df5babaca73e714359e8b6`

- Label: **market_maker**
- Reasons: rfq_maker_fills=2>=2
- Activity: AMM=0 buy=2 sell=0 RFQ maker=2 taker=0 transfers=0
- Notional (sum USD proxy): 30 (median 15)
- Explorer: https://mantlescan.xyz/address/0xd894965581a598c0b9df5babaca73e714359e8b6

### Rebalancers (CEX-touch) (`rebalancer`)

| Address | Contract? | Pairs | AMM | RFQ m/t | Conv | Mean-rev | CEX touches | Why |
|---------|-----------|------:|----:|--------:|-----:|---------:|------------:|-----|
| [`0x513ab093…`](https://mantlescan.xyz/address/0x513ab093c745d21357475aea9a80f6ca4974ce00) | no | 1 (SPCXx) | 0 | 0/0 | — | 0.49 | 213 | cex_touch_transfers=213 |
| [`0xfd8b2cd9…`](https://mantlescan.xyz/address/0xfd8b2cd9ad70a25f6741ac8948069000d42edf1a) | no | 4 (CRCLx,GOOGLx,HOODx,TSLAx) | 0 | 0/0 | — | 0.67 | 82 | cex_touch_transfers=82 |
| [`0x58884621…`](https://mantlescan.xyz/address/0x588846213a30fd36244e0ae0ebb2374516da836c) | no | 7 (AAPLx,COINx,CRCLx,GOOGLx) | 0 | 0/0 | — | 0.08 | 51 | cex_touch_transfers=51 |
| [`0xd8169f09…`](https://mantlescan.xyz/address/0xd8169f099ce16c87a99d2a8494023574b5eea9c5) | no | 4 (CRCLx,HOODx,METAx,TSLAx) | 0 | 0/0 | — | 0.21 | 24 | cex_touch_transfers=24 |
| [`0x35263632…`](https://mantlescan.xyz/address/0x35263632aa76b909b1ce57bd6291b80c565f79b3) | no | 6 (CRCLx,GOOGLx,HOODx,METAx) | 0 | 0/0 | — | 0.56 | 22 | cex_touch_transfers=22 |
| [`0x4a67e97e…`](https://mantlescan.xyz/address/0x4a67e97e770de93952b8596f04c13ada0ab9a69c) | no | 5 (COINx,CRCLx,GOOGLx,HOODx) | 0 | 0/0 | — | 0.22 | 21 | cex_touch_transfers=21 |
| [`0x0d4dc3b8…`](https://mantlescan.xyz/address/0x0d4dc3b8becc98782309e443a6da4b9455b5ca48) | no | 4 (CRCLx,HOODx,METAx,NVDAx) | 0 | 0/0 | — | 0.18 | 14 | cex_touch_transfers=14 |
| [`0x75094d26…`](https://mantlescan.xyz/address/0x75094d260b86ab7ac5c926404f5fbad4c79df581) | no | 1 (SPCXx) | 0 | 0/0 | — | 0.49 | 14 | cex_touch_transfers=14 |
| [`0xc868d0ea…`](https://mantlescan.xyz/address/0xc868d0ea71243f1580f934cdc59620603bf9f1f1) | no | 3 (CRCLx,GOOGLx,HOODx) | 0 | 0/0 | — | 0.22 | 13 | cex_touch_transfers=13 |
| [`0xc9d16f4c…`](https://mantlescan.xyz/address/0xc9d16f4cd9edec605689127794a1091c4a6098ae) | no | 1 (SPCXx) | 0 | 0/0 | — | 0.43 | 11 | cex_touch_transfers=11 |
| [`0x03972795…`](https://mantlescan.xyz/address/0x0397279515ba8a57a869daa97df4dd7562cbf648) | no | 1 (SPCXx) | 0 | 0/0 | — | 0.50 | 10 | cex_touch_transfers=10 |
| [`0x5df5012c…`](https://mantlescan.xyz/address/0x5df5012c151a75ddd808cabee83de64b5c4afe63) | no | 1 (SPCXx) | 0 | 0/0 | — | 0.94 | 7 | cex_touch_transfers=7 |
| [`0xcade76da…`](https://mantlescan.xyz/address/0xcade76da0f44f00ad2c27f8e74a333ae084c9817) | no | 1 (SPCXx) | 0 | 0/0 | — | 0.56 | 7 | cex_touch_transfers=7 |
| [`0xfe279ea4…`](https://mantlescan.xyz/address/0xfe279ea42b394168be9ab5e34c247de43a1a4d4a) | no | 1 (SPCXx) | 0 | 0/0 | — | 0.43 | 6 | cex_touch_transfers=6 |
| [`0x9965680d…`](https://mantlescan.xyz/address/0x9965680df158c11a5e27bcc33aa3f462139c6dec) | no | 1 (SPCXx) | 0 | 0/0 | — | 0.40 | 4 | cex_touch_transfers=4 |

#### `0x513ab093c745d21357475aea9a80f6ca4974ce00`

- Label: **rebalancer**
- Reasons: cex_touch_transfers=213
- Activity: AMM=0 buy=0 sell=0 RFQ maker=0 taker=0 transfers=279
- Notional (sum USD proxy): 0 (median 0)
- Explorer: https://mantlescan.xyz/address/0x513ab093c745d21357475aea9a80f6ca4974ce00

#### `0xfd8b2cd9ad70a25f6741ac8948069000d42edf1a`

- Label: **rebalancer**
- Reasons: cex_touch_transfers=82
- Activity: AMM=0 buy=0 sell=0 RFQ maker=0 taker=0 transfers=82
- Notional (sum USD proxy): 0 (median 0)
- Explorer: https://mantlescan.xyz/address/0xfd8b2cd9ad70a25f6741ac8948069000d42edf1a

#### `0x588846213a30fd36244e0ae0ebb2374516da836c`

- Label: **rebalancer**
- Reasons: cex_touch_transfers=51
- Activity: AMM=0 buy=0 sell=0 RFQ maker=0 taker=0 transfers=65
- Notional (sum USD proxy): 0 (median 0)
- Explorer: https://mantlescan.xyz/address/0x588846213a30fd36244e0ae0ebb2374516da836c

#### `0xd8169f099ce16c87a99d2a8494023574b5eea9c5`

- Label: **rebalancer**
- Reasons: cex_touch_transfers=24
- Activity: AMM=0 buy=0 sell=0 RFQ maker=0 taker=0 transfers=35
- Notional (sum USD proxy): 0 (median 0)
- Explorer: https://mantlescan.xyz/address/0xd8169f099ce16c87a99d2a8494023574b5eea9c5

#### `0x35263632aa76b909b1ce57bd6291b80c565f79b3`

- Label: **rebalancer**
- Reasons: cex_touch_transfers=22
- Activity: AMM=0 buy=0 sell=0 RFQ maker=0 taker=0 transfers=90
- Notional (sum USD proxy): 0 (median 0)
- Explorer: https://mantlescan.xyz/address/0x35263632aa76b909b1ce57bd6291b80c565f79b3

### Arb bots (high convergence) (`arb_bot`)

| Address | Contract? | Pairs | AMM | RFQ m/t | Conv | Mean-rev | CEX touches | Why |
|---------|-----------|------:|----:|--------:|-----:|---------:|------------:|-----|
| [`0xcdddbcb6…`](https://mantlescan.xyz/address/0xcdddbcb65f01d7696ecca0e635f425ce4d429010) | no | 5 (AAPLx,GOOGLx,METAx,NVDAx) | 6216 | 0/0 | 0.97 (n=6215) | 0.45 | 0 | convergence=0.97 on 6215 scored; bybit_align=0.91 |
| [`0xb15d7daa…`](https://mantlescan.xyz/address/0xb15d7daa4e21500102c7c57b21009cf3549ab890) | no | 6 (AAPLx,CRCLx,GOOGLx,HOODx) | 114 | 0/0 | 1.00 (n=114) | 0.47 | 108 | convergence=1.00 on 114 scored; bybit_align=0.58 |
| [`0xed8f393d…`](https://mantlescan.xyz/address/0xed8f393d2582c9b2ef57559fa5c49f5e5b6d543a) | no | 6 (CRCLx,GOOGLx,HOODx,METAx) | 52 | 0/0 | 1.00 (n=52) | 0.45 | 15 | convergence=1.00 on 52 scored; bybit_align=0.50 |

#### `0xcdddbcb65f01d7696ecca0e635f425ce4d429010`

- Label: **arb_bot**
- Reasons: convergence=0.97 on 6215 scored, bybit_align=0.91
- Activity: AMM=6216 buy=2975 sell=3241 RFQ maker=0 taker=0 transfers=6216
- Notional (sum USD proxy): 135667.4677296234560979792137 (median 22.00446174290725042769856289)
- Explorer: https://mantlescan.xyz/address/0xcdddbcb65f01d7696ecca0e635f425ce4d429010

#### `0xb15d7daa4e21500102c7c57b21009cf3549ab890`

- Label: **arb_bot**
- Reasons: convergence=1.00 on 114 scored, bybit_align=0.58
- Activity: AMM=114 buy=41 sell=73 RFQ maker=0 taker=0 transfers=462
- Notional (sum USD proxy): 57187.60486727872691308677324 (median 503.824379257082621681932596)
- Explorer: https://mantlescan.xyz/address/0xb15d7daa4e21500102c7c57b21009cf3549ab890

#### `0xed8f393d2582c9b2ef57559fa5c49f5e5b6d543a`

- Label: **arb_bot**
- Reasons: convergence=1.00 on 52 scored, bybit_align=0.50
- Activity: AMM=52 buy=48 sell=4 RFQ maker=0 taker=0 transfers=200
- Notional (sum USD proxy): 4758.556247803153395201879043 (median 89.99521669315367529489494725)
- Explorer: https://mantlescan.xyz/address/0xed8f393d2582c9b2ef57559fa5c49f5e5b6d543a

### Price keepers (small bidirectional AMM) (`price_keeper`)

_None above draft thresholds in this window._

## RFQ maker evidence (strongest MM signal)

Decoded **18** LOP `OrderFilled` events. Maker is recovered from Approval→LOP or first external stock sender; taker is the other non-infra external on the Transfer graph. Settlement routinely routes through `0x41dee1855293e4450cd67459047f372d4d818143` and fee hops `0x5f7a4c11…` / `0x3fdcb4af…` / `0x68a35992…` — treated as infra, not MM.

| ts (UTC) | pair | maker | taker | side | USDC | tx |
|----------|------|-------|-------|------|-----:|----|
| 2026-07-06 16:14 | ? | `0xd8949655…` | — | buy_native | 15 | [tx](https://mantlescan.xyz/tx/0x02369289e789a7100eb47e9298015116f81454ab8db368ad175a247af7d12a41) |
| 2026-07-07 02:59 | ? | `0x804b1024…` | — | buy_native | 30 | [tx](https://mantlescan.xyz/tx/0x4cb6be5220dc931fbe784da78f6564a62b5737c69a84efff7c7fa987e7b6461b) |
| 2026-07-07 14:19 | ? | `0xb4a4a76e…` | — | buy_native | 500 | [tx](https://mantlescan.xyz/tx/0xb16cb74a930d258a53a0ea04acc940db94d2b13a3587fb4f9558a9078d185642) |
| 2026-07-07 15:03 | ? | `0xb4a4a76e…` | — | buy_native | 420.634794 | [tx](https://mantlescan.xyz/tx/0x36456d8da92f9b7cf98b3779dd3080f3bbf9eed85fae07668563c5cdd61b748b) |
| 2026-07-08 19:49 | ? | `0x1baab0b2…` | — | buy_native | 34 | [tx](https://mantlescan.xyz/tx/0x8419e3d879e02677fcedaf5ca5c81f6c66a76d082f319af4e21424da7df1a1d7) |
| 2026-07-08 19:50 | ? | `0x1baab0b2…` | — | buy_native | 35 | [tx](https://mantlescan.xyz/tx/0x27e47d92b2324543d59891657d5397dfa1ee8602a01165ff386b5b58a7806d6e) |
| 2026-07-08 19:51 | ? | `0x1baab0b2…` | — | buy_native | 1500 | [tx](https://mantlescan.xyz/tx/0xb776050ffb0d00c581eea534760823e8e0c81c5df1f17ee75f15ca1acf2b5a3f) |
| 2026-07-08 19:56 | ? | `0x1baab0b2…` | — | buy_native | 350 | [tx](https://mantlescan.xyz/tx/0x8fe11611d1bade4fa5b66f32aa3b06a44eda00fbd9f034ae13b8eb5e60c7c7ee) |
| 2026-07-10 09:26 | SPCXx | `0x1baab0b2…` | — | buy_native | 49.966598 | [tx](https://mantlescan.xyz/tx/0xfcc51532f20f3a8b8cd4177fc36045dfacd60d6255efd6b22fe287de991a1e1d) |
| 2026-07-10 17:21 | ? | `0x1baab0b2…` | — | sell_native | 626.1336 | [tx](https://mantlescan.xyz/tx/0x0221a39a6e60851528ebee1bb5e2d2a8578e103563e145cf80d8cd75aae0ed6e) |
| 2026-07-10 17:22 | ? | `0x1baab0b2…` | — | sell_native | 679.498937 | [tx](https://mantlescan.xyz/tx/0x94991e3bc50b67ee05474eef9e13794ca792bcefc20de4dedad20f235879d031) |
| 2026-07-13 13:32 | ? | `0x2b90c1dc…` | — | buy_native | 11 | [tx](https://mantlescan.xyz/tx/0x7a7112150878d07971e3df680d21dc9544a11f9841fbdc0c7cb4c140fafae06d) |
| 2026-07-13 13:34 | ? | `0x2b90c1dc…` | — | sell_native | 10.999766 | [tx](https://mantlescan.xyz/tx/0xf7115983f593cd7b80bb101fa0f29495fce020ce8b1c65dbddc765aa9bd741c6) |
| 2026-07-13 19:38 | ? | `0x2ae39446…` | — | sell_native | 724.673606 | [tx](https://mantlescan.xyz/tx/0x2a65c4fc36c4dfdaf916a24db45a998926a8e87ca6a81a88bd94dab93b5e224d) |
| 2026-07-15 03:44 | ? | `0xd8949655…` | — | buy_native | 15 | [tx](https://mantlescan.xyz/tx/0xcb793d7467177e040381e86ba8394964060d504a322c9b04427e3ceb12490b78) |
| 2026-07-16 09:44 | SPCXx | `0x804b1024…` | — | sell_native | 86.42847 | [tx](https://mantlescan.xyz/tx/0x01c0685b62701242f07a1ec71a2c2d9d3643d70b50ed609223839fd066884801) |
| 2026-07-21 08:18 | ? | `0x804b1024…` | — | buy_native | 11 | [tx](https://mantlescan.xyz/tx/0x081c7983f0ac26e068bcf1ec702fb57c77fd9efef90dc95bf250836acfd471a2) |
| 2026-07-21 08:37 | ? | `0x9d126d35…` | — | buy_native | 11 | [tx](https://mantlescan.xyz/tx/0x096b724679ebda1354e0bbb363bebbd2cfb6b0c28529d00f142c586f8deb94c0) |

### RFQ maker leaderboard (all decoded fills)

| Maker | Fills as maker | Label | Pairs |
|-------|---------------:|-------|-------|
| [`0x1baab0b2e7…`](https://mantlescan.xyz/address/0x1baab0b2e787eff5b3753addf31a1bcdc2417f9b) | 7 | `market_maker` | SPCXx,UNKNOWN |
| [`0x804b10246b…`](https://mantlescan.xyz/address/0x804b10246b7d495517411b788f46134e731779cb) | 3 | `market_maker` | SPCXx,UNKNOWN |
| [`0xb4a4a76e99…`](https://mantlescan.xyz/address/0xb4a4a76e99e2acc1e51567c0231628780dfab0c8) | 2 | `market_maker` | SPCXx,UNKNOWN |
| [`0x2b90c1dcfc…`](https://mantlescan.xyz/address/0x2b90c1dcfc676be2eb2ffaac1b37ac5f5c04662e) | 2 | `market_maker` | METAx,UNKNOWN |
| [`0xd894965581…`](https://mantlescan.xyz/address/0xd894965581a598c0b9df5babaca73e714359e8b6) | 2 | `market_maker` | UNKNOWN |
| [`0x2ae394468e…`](https://mantlescan.xyz/address/0x2ae394468edf2907ac5cf77b5472f935804208fa) | 1 | `unknown` | UNKNOWN |
| [`0x9d126d3589…`](https://mantlescan.xyz/address/0x9d126d3589320bb83dc6de50795b8ed7a0b0bdaf) | 1 | `unknown` | UNKNOWN |

## Rebalance / CEX deposit clustering

Heuristic: addresses with high unique-counterparty degree on native/wrapper Transfer graphs (deposit-like). **Not** verified Mantlescan labels — candidates for manual check + hot-wallet list in WHI-768.

| Address | Counterparties | Transfers | Volume (token units) |
|---------|---------------:|----------:|---------------------:|
| [`0xd14b0dcd31…`](https://mantlescan.xyz/address/0xd14b0dcd319551ae4d7b12787c00ee1c1f9e1d2e) | 29 | 208 | 86.342468065220794042 |
| [`0x1a43997eb0…`](https://mantlescan.xyz/address/0x1a43997eb0513300ef9bb7111a836870598a427f) | 17 | 473 | 1318.304302900760206243 |
| [`0x9941b4c49e…`](https://mantlescan.xyz/address/0x9941b4c49e967c974d441be437929055879e247c) | 9 | 84 | 13.330348193527702540 |
| [`0x588846213a…`](https://mantlescan.xyz/address/0x588846213a30fd36244e0ae0ebb2374516da836c) | 7 | 65 | 265.157671460769987336 |
| [`0xd8169f099c…`](https://mantlescan.xyz/address/0xd8169f099ce16c87a99d2a8494023574b5eea9c5) | 7 | 35 | 222.748182101780677493 |
| [`0xb15d7daa4e…`](https://mantlescan.xyz/address/0xb15d7daa4e21500102c7c57b21009cf3549ab890) | 6 | 462 | 2580.937534041878986127 |
| [`0xfd8b2cd9ad…`](https://mantlescan.xyz/address/0xfd8b2cd9ad70a25f6741ac8948069000d42edf1a) | 6 | 82 | 506.062144437581438050 |
| [`0x0d4dc3b8be…`](https://mantlescan.xyz/address/0x0d4dc3b8becc98782309e443a6da4b9455b5ca48) | 6 | 27 | 68.646035518557335566 |
| [`0xc868d0ea71…`](https://mantlescan.xyz/address/0xc868d0ea71243f1580f934cdc59620603bf9f1f1) | 6 | 25 | 116.330134187169630426 |
| [`0x35263632aa…`](https://mantlescan.xyz/address/0x35263632aa76b909b1ce57bd6291b80c565f79b3) | 5 | 90 | 60.802042289922805060 |
| [`0xed8f393d25…`](https://mantlescan.xyz/address/0xed8f393d2582c9b2ef57559fa5c49f5e5b6d543a) | 4 | 200 | 167.311362145634117990 |
| [`0x4a67e97e77…`](https://mantlescan.xyz/address/0x4a67e97e770de93952b8596f04c13ada0ab9a69c) | 4 | 27 | 116.070043115474490734 |

## Draft machine-checkable rules (feed WHI-768)

Priority order (first match wins): market_maker → arb_bot → rebalancer → price_keeper → retail → unknown. Thresholds are defaults in `DraftThresholds` (M4 gates match `config/attribution.yaml`).

### `market_maker`

Either:

1. **RFQ maker path (strong):** `n_rfq_maker ≥ 2` on decoded LOP fills (maker = Approval→LOP owner or first external stock sender).
2. **Cross-pair inventory path:** `n_pairs ≥ 2` AND `n_amm ≥ 10` AND both directions with share ≥ 0.25 AND `median_notional_usd ≤ 500` AND (`inv_mean_reversion` is null OR ≥ 0.55).

**Relation to M4 `price_keeper`:** price_keeper is the *small* bidirectional AMM re-pegger without RFQ maker evidence. An address that also clears RFQ-maker or cross-pair mean-reverting inventory upgrades to `market_maker`. Do not double-label.

### `rebalancer`

`cex_touch_transfers ≥ 3` where a touch is a native/wrapper Transfer whose counterparty ∈ CEX cluster set (cluster gate: degree/volume heuristics; min notional 1 native when filtering noise).

Orthogonal to MM: the same desk may be both; product may emit `market_maker+rebalancer` flags instead of mutually exclusive labels.

### Existing M4 labels (unchanged semantics)

- **`arb_bot`:** `n_convergence_scored ≥ 20` and `convergence_ratio ≥ 0.8` (same spirit as `m4-attribution-labels.md`).
- **`price_keeper`:** `n_amm ≥ 10`, both dirs ≥ 0.25, median ≤ 500, max ≤ 2000.
- **`retail` / `unknown`:** residual with `n_trade ≥ 5` vs thin sample.

### Fields required in the collector (WHI-768 input)

| Field | Source | Why |
|-------|--------|-----|
| `fluxion_rfq_fills.maker` | receipt decode / LOP order | strongest MM signal |
| `fluxion_rfq_fills.taker` | receipt decode | taker behavior / flow |
| `fluxion_rfq_fills.pair_id` | token map | pair-scoped RFQ share |
| `fluxion_rfq_fills.direction` / amounts | Transfer legs | inventory + notional |
| `erc20_transfers` (native + wrapper) | `eth_getLogs` Transfer | rebalance + inventory completeness |
| optional `cex_wallets` allowlist | config | stable rebalance label |
| existing `fluxion_swaps.*` + Bybit mid join | already collected | convergence / arb_bot |

## Data gaps / caveats

1. **Collector journal (VPS) is not the analysis source for trades.** As of analysis time it held ~14h of tape starting 2026-08-01 (weekend) with **1** AMM swap and **0** RFQ fills. Historical AMM/RFQ/Transfer were backfilled via Mantle `eth_getLogs`.
2. **Wrapper vs native double-count:** Transfer pull includes both native and wrapper ERC-20s under the same `pair_id`. Inventory path for wrap/unwrap can double-move; WHI-768 should pick one inventory asset (prefer native) or net wrap events.
3. **RFQ token map misses:** 16/18 fills in the 30d sample touch ERC-20s **outside** inventory native/wrapper (top: `0x7796f4e2…`, `0x58100046…`, `0x368192fe…`, `0x90a2a4c7…`) → `pair_id` often null / `UNKNOWN`. Maker recovery still works; WHI-768 should resolve token→pair via `asset()` on wrappers or an expanded token registry before pair-scoped RFQ share ships.
4. **Bybit mid join is 1m kline**, not the live L1 book — fine for convergence ratios over days, not for sub-second lead-lag.
5. **Address clustering is partial.** Contract vs EOA is probed for up to 200 ledger addresses; deployer/funding-source traces and behavior-similarity clustering are **not** implemented in this pass (scope cut for WHI-767; WHI-768 may pick them up).
6. **CEX wallets are clustered, not labeled.** Manual Mantlescan / Bybit deposit address verification still required before shipping `rebalancer` as a product label.
7. **LP Mint/Burn not pulled.** MM LP behavior is out of scope for this pass; only swap/fill/transfer inventory.
8. **Pools without AMM** (AMZNx/COINx/MCDx) contribute Transfer-only rows; no swap-based convergence.
9. **Cross-pair MM inventory path** (bidirectional + mean-reversion across ≥2 pairs) is implemented but did **not** fire in the 30d sample — all five `market_maker` hits came from the RFQ-maker path. Thresholds for that branch are unfitted on live xStock flow.

## Relationship to M4

M4 (`arb_bot` / `price_keeper` / `retail` / `unknown`) remains the behavior layer for **AMM takers**. This research adds:

- **RFQ maker** as a first-class economic role (not a taker).
- **Inventory mean-reversion** and **cross-pair** activity.
- **CEX transfer touches** for rebalance.

Product recommendation for WHI-768: keep M4 labels for AMM takers; add orthogonal flags `is_rfq_maker`, `is_rebalancer`, and promote combined MM when RFQ-maker or cross-pair inventory rules fire.

## Reproduction

```bash
# optional keyed RPC
export MANTLE_RPC_URL=...   # wss-tob rewritten to https rpc-tob
uv run python scripts/mm_attribution_analysis.py --days 30
# reuse cache:
uv run python scripts/mm_attribution_analysis.py --skip-fetch
```

Artifacts:

- `data/mm_analysis/swaps.json`, `rfq_fills.json`, `transfers.json`, `bybit_mids.json`
- `data/mm_analysis/result.json` — full labeled table
- `src/monitor/attribution/mm_draft.py` — pure rules
- `tests/monitor/attribution/test_mm_draft.py`

