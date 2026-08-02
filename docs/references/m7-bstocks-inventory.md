# M7-1 bStocks × PancakeSwap inventory (WHI-770)

Companion to `config/markets/binance-pancake.yaml` (M7-2 market schema; inventory under `inventory:`).
Snapshot date: **2026-08-02**.

## Method

1. **Binance spot list** — `GET https://api.binance.com/api/v3/exchangeInfo` (reachable from
   SG research host; **not** from the US VPS — see § Geo). Heuristic for inventory:
   `quoteAsset == USDT`, `status == TRADING`, base ends with `B` (plus special `MUB`),
   excluding known crypto bases (`BNB`, `SHIB`, `ARB`, `QNTB`, …). Yielded **55** spot
   pairs at inventory time. PR/AUM notes claim “46+ listings”; the exchangeInfo set is the
   operational universe for collectors.
2. **PancakeSwap pools** — DexScreener search per base on `chainId=bsc`, `dexId` containing
   `pancake` (covers V2 + V3 labels in the API). Prefer USDT quote. **Every candidate pool
   was re-verified on-chain** via `token0()` / `token1()` against the BEP-20 address
   (DexScreener occasionally attaches a high-TVL pool to the wrong token — QQQB and SNDKB
   were false positives and dropped). **No StableSwap pool** appeared in DexScreener
   results for these symbols; surviving verified USDT pools are all **V3** (`fee()`
   present). V2 pairs that showed up were either non-USDT (WBNB) or dust and lost the
   USDT-prefer rank. **Limitation:** negative claims for V2/StableSwap rest on the
   indexer + USDT-prefer filter — not a PCS V2 factory `getPair` or StableSwap registry
   enumeration (open risk).
3. **Liquidity rank** — DexScreener `liquidity.usd` on the verified best USDT pool.
   Top **10** by that metric form the monitor set. Est. TVL is an inventory snapshot, not
   live journal data.
4. **On-chain enrichment** — public BSC RPC `https://bsc-dataseed.binance.org`:
   `fee()`, `slot0()` → AMM mid, `decimals()`, BEP-677 `uiMultiplier()`.
5. **Price cross-check** — Binance `bookTicker` mid vs pool mid (raw token / USDT).
   Snapshot taken **2026-08-02 Sunday** (US equities **closed**); weekend basis can
   widen CEX⇄AMM mids (cf. WHI-753 closed-session work). Re-check on a NYSE open day
   before treating bps columns as open-session typical.
6. **Geo** — REST/WS probes from deploy VPS `whi715-vps` / `107.175.234.202`
   (Los Angeles, US) vs research host (Singapore).
7. **Dex coverage** — DexScreener search was run for **all 55** Appendix A bases
   (sequential, rate-limited); on-chain verification then filtered to pools whose
   `token0/1` match the resolved BEP-20.

## Product background (primary sources)

| Fact | Source |
|------|--------|
| Issuer BTech Holdings (Binance affiliate); 1:1 backed US securities; ADGM-class product | [Binance Academy — What Are bStocks?](https://www.binance.com/en/academy/articles/what-are-bstocks-a-guide-to-tokenized-stocks-on-binance), [Intro announcement](https://www.binance.com/en/support/announcement/detail/2c0c92ed15ac42d1b14bb1eac00d22bb) |
| Launch 2026-06-11/12: CRCLB, MUB, NVDAB, SNDKB, TSLAB | Intro + [PancakeSwap blog](https://blog.pancakeswap.finance/articles/bstocks-on-pancakeswap) |
| Later expansions (e.g. 10 pairs 2026-07-29 incl. AAPLB/AMZNB/…) with BSC contract links | [Adds 10 bStocks](https://www.binance.com/en/support/announcement/detail/fd3c0f17a7504eb5be1cb1911c6da0cd) |
| BEP-20 on BSC; deposit/withdraw **BSC only** | [Deposit/withdraw FAQ](https://www.binance.com/en/support/faq/detail/f0d41139fadc4790bf9a4c0c7bce2e88) |
| Pancake: regular Swap **or** Tokenized Stocks Terminal UI | [PancakeSwap blog](https://blog.pancakeswap.finance/articles/bstocks-on-pancakeswap) → https://pancakeswap.finance/stocks |
| Multiplier / corporate actions | [bStocks FAQ](https://www.binance.com/en/support/faq/detail/f0c03cd6509a4085b4cce1636f16be38), [BEP-677](https://github.com/bnb-chain/BEPs/blob/master/BEPs/BEP-677.md) |

## Top 10 verified pairs (Binance spot ∩ liquid Pancake USDT)

Ranked by DexScreener liquidity USD on the **on-chain-verified** best USDT pool.
`low_liquidity` uses the same $50k gate as M1 `pairs.yaml` (inventory convention only).

### Web overview badges (WHI-781) — not a volume top-10 inventory

The Web overview can show compact **`TVL`** / **`Vol`** badges next to a pair id.
These **label two independent ranking dimensions** on the *current fixed inventory*;
they do **not** mean the monitor set was rebuilt to Binance CEX volume top-10.

| Badge | Dimension | Default rule (binance-pancake) |
|-------|-----------|--------------------------------|
| `TVL` | DEX pool liquidity | Inventory `est_liquidity_usd` rank ≤ 5 among **all** market overview rows (not UI-filtered) |
| `Vol` | CEX 24h quote volume | Live `cex_volume_24h` rank ≤ 5 among **all** market overview rows (not UI-filtered) |

A pair may show **both** when it ranks high on both axes (set overlap with global
CEX leaders is small — research day 2026-08-02 had only SPCXB / SKHYB / MUB in both
universes). The `low-liq` badge was removed from the UI; the hide-low-liquidity
filter still uses inventory `low_liquidity`. bybit-fluxion uses **Vol-only** (no
PCS TVL story).

| # | id | Binance | BEP-20 | PCS pool | fee | Est liq USD | uiMultiplier | mid cross-check |
|---|-----|---------|--------|----------|-----|-------------|--------------|-----------------|
| 1 | SPCXB | SPCXBUSDT | `0xbe9D…03E1` | `0x977D…5b4d` V3 | 2500 | ~3.17M | 1.0 | ~−1 bps |
| 2 | SKHYB | SKHYBUSDT | `0xCA75…DB61` | `0xD7d3…aD52` V3 | 2500 | ~577k | 1.0 | ~+38 bps |
| 3 | TSLAB | TSLABUSDT | `0x5b19…292f` | `0xB0f5…9c41` V3 | 2500 | ~260k | 1.0 | ~−1 bps |
| 4 | SPYB | SPYBUSDT | `0x7138…82D8` | `0x7aA6…ddbF` V3 | **100** | ~257k | 1.0 | ~−12 bps |
| 5 | NVDAB | NVDABUSDT | `0x02Fc…7436` | `0x8FB4…690C` V3 | 2500 | ~228k | 1.0 | ~−26 bps |
| 6 | AAPLB | AAPLBUSDT | `0x431a…9b7A` | `0xe9b9…7365` V3 | 2500 | ~125k | 1.0 | ~−35 bps |
| 7 | GOOGLB | GOOGLBUSDT | `0x3F53…3238` | `0x8900…8144` V3 | 2500 | ~77k | 1.0 | ~−14 bps |
| 8 | MSFTB | MSFTBUSDT | `0x8010…B9b0` | `0x58e4…5B44` V3 | **10000** | ~44k | 1.0 | ~+80 bps |
| 9 | INTCB | INTCBUSDT | `0xe614…5283` | `0x4dD8…2481` V3 | 2500 | ~16k | 1.0 | ~−9 bps |
| 10 | MUB | MUBUSDT | `0xcdf2…2699` | `0x9E75…071e` V3 | 2500 | ~9k | **1.0001075…** | ~−12 bps |

Full addresses and YAML: `config/markets/binance-pancake.yaml`.

**Quote asset:** all top pools are **BSC USDT**
(`0x55d398326f99059fF775485246999027B3197955`, 18 decimals) — not BUSD-era.
Binance spot legs are also USDT → **no USDT/USDC basis** for this market (unlike Bybit⇄Fluxion).

**Decimals:** top-10 BEP-20s are **18** (verified). (Some non-top tokens in the wider set
use other decimals — always read `decimals()`.)

### Not in top 10 (notable)

| Asset | Why |
|-------|-----|
| QQQB | DexScreener listed a multi-million “QQQB/USDT” pool; on-chain `token0/1` did **not** contain the QQQB BEP-20 → **false positive**, excluded. |
| SNDKB, CRCLB | Launch names; no verified liquid PCS USDT pool for the exchangeInfo token address at inventory time (or only dust). |
| AMZNB, PYPLB, GSB, … | Binance-listed; pancake USDT pool missing or dust after verification. |
| SOXLB (~$6k), MUUB (broken mid) | Verified but below top 10. |

Of the **55** bases searched, **12** had a verified Pancake USDT pool after on-chain
filter; liquidity is highly concentrated in the first ~7 of the top-10.

## Multiplier semantics (比价命门)

### Official description

Binance FAQ ([detail](https://www.binance.com/en/support/faq/detail/f0c03cd6509a4085b4cce1636f16be38)):

- Multiplier reflects corporate actions (dividend reinvestment after ~30% US withholding,
  splits, reverse splits, …).
- **Raw on-chain balance does not change** for these adjustments.
- **Displayed / UI balance** = raw × multiplier.
- Splits: e.g. 10-for-1 → multiplier ×10, display qty ×10, **price per display unit ÷10**.
- Explorers/wallets that integrate the extension show UI amounts; raw `balanceOf` stays.

Academy/deposit FAQ sometimes say “on-chain rebasing mechanism.” That wording is
**misleading relative to classic rebase tokens**: there is **no** `balanceOf` change and
**no** mint/transfer on dividend; the adjustment is a **UI scale factor**.

### On-chain standard

Intro materials reference **BEP-677** (EIP-8056 Scaled UI Amount on BSC):

- Spec: https://github.com/bnb-chain/BEPs/blob/master/BEPs/BEP-677.md
- Core getter: `uiMultiplier() → uint256` with **1e18 = 1.0×**
- `uiAmount = rawAmount * uiMultiplier / 1e18`
- Optional: `toUIAmount` / `fromUIAmount`, `balanceOfUI`, scheduled `newUIMultiplier`

Empirically on top tokens (2026-08-02): `uiMultiplier()` returns `1e18` (1.0) for all
top-10 **except MUB ≈ 1.0001075** (consistent with small dividend reinvestment).

**Binance REST `exchangeInfo` has no multiplier field** for these symbols (unlike Bybit
`xstockMultiplier` on instruments-info). Live mult **must** be read on-chain via
`uiMultiplier()` (or Binance account UI / future private API if discovered). Snapshot
values in YAML are inventory-time only.

### Compare to Bybit xStocks

| | Bybit xStocks | bStocks (BEP-677) |
|--|---------------|-------------------|
| CEX field | `xstockMultiplier` on instruments-info | none on public exchangeInfo |
| Token qty | unchanged; mult is a **price** factor | raw qty unchanged; mult scales **UI qty** |
| M1 formula | `comparable = bybit_mid / multiplier` | see below |
| On-chain | no scaled-UI standard on Mantle xStock | `uiMultiplier()` on BEP-20 |

### Pricing formula (recommended for M7 metrics)

AMM pools trade **raw** ERC-20 units. Binance spot after a mult change prices the
**display** unit (FAQ split example). Economic identity:

```
value = binance_display_mid * ui_qty
      = binance_display_mid * (raw_qty * uiMultiplier / 1e18)
      = amm_raw_mid * raw_qty

⇒  amm_raw_mid  ≈  binance_display_mid * (uiMultiplier / 1e18)
```

or, in display space:

```
comparable_binance_as_raw = binance_mid * ui_multiplier_float
spread uses (amm_mid - comparable_binance_as_raw) / …
```

At `uiMultiplier == 1.0` (almost all names today), raw mid ≈ Binance mid within a few
to tens of bps on liquid names (TSLAB ~−1 bps, SPCXB ~−1 bps) — a **consistency check
only** (multiply vs divide are indistinguishable when mult≈1). The **multiply**
direction is **doc-derived** (FAQ 10-for-1 split: display qty ×10, price per display
÷10 ⇒ raw notional = display_mid × ui_mult) plus BEP-677, not empirically separated
on this snapshot (MUB’s mult 1.0001 is ~1 bp, below mid noise). Validate across a real
split/dividend event when one lands.

**Do not reuse Bybit’s divide formula** (`monitor.symbols.de_multiplied_price`) without
flipping the model. Draft YAML uses field name **`ui_multiplier`** (not `multiplier`)
so M7-2 cannot silently wire the Bybit helper.

When mult moves (splits), failing to apply this will look like a 10× “arb.”

## Tokenized Stocks Terminal / mechanism layer

| Question | Finding |
|----------|---------|
| Is the Terminal RFQ / quote-vendor? | **No evidence.** Pancake blog: trade via **regular Swap** or the Stocks Terminal UI at `/stocks`. Same AMM surface, dedicated front-end. |
| Oracle intervention in AMM? | No public oracle-pegging of pool price found. AMM mid free-floats vs Binance (observed 1–80 bps on inventory snapshot). “Oracle” language in marketing is about **underlying share valuation / PoC**, not PCS pool control. |
| RFQ-style attribution (M4)? | **Not applicable** for bStocks v1. Mechanism is **AMM-only** (PCS V3). No Fluxion-like LOP/RFQ poll target. |
| Secondary venues | DexScreener also shows some Uniswap-on-BSC and meme decoys; monitor scope is **Pancake V3 USDT** only. |

## ⚠️ Binance geo-block (actionable for M7-6)

### Empirically measured on deploy VPS

Host: `107.175.234.202` (`whi715-vps`, RackNerd / HostPapa), **Los Angeles, US**
(`ipinfo`: country=US).

| Endpoint | Result from VPS |
|----------|-----------------|
| `https://api.binance.com/api/v3/ping` | **HTTP 451** — restricted location |
| `https://api1.binance.com` / `api-gcp.binance.com` | **451** |
| `https://stream.binance.com:9443` WS upgrade | **451** |
| `https://data-api.binance.vision/api/v3/ping` | **200** `{}` |
| `…/ticker/bookTicker?symbol=TSLABUSDT` | **200** live book |
| `…/depth`, `…/exchangeInfo` | **200** |
| `wss://data-stream.binance.vision/ws/…` | **101 Switching Protocols** (WS upgrade succeeds; market-data path) |
| `https://api.binance.us` | 200 but **different venue** (no bStocks) — do not use |

Research host in **Singapore** reaches `api.binance.com` with 200.

### Executable recommendation (feed M7-6)

1. **Keep the US VPS for Mantle/Bybit + Web API** if desired; for **Binance market data
   only**, point the Binance collector at:
   - REST base: `https://data-api.binance.vision`
   - WS base: `wss://data-stream.binance.vision`
   Official market-data mirror; **no trading / user-data** (sufficient for the
   monitor panel).
2. **Do not** expect signed trading or withdraw APIs from this IP without a tunnel.
3. **Alternatives if vision proves incomplete** (depth streams, limits, future private
   endpoints):
   - **Non-US VPS / sidecar** in SG/EU/JP for `api.binance.com` + `stream.binance.com`
     (cleanest long-term if we ever need user streams).
   - **HTTPS proxy / WireGuard egress** from US VPS to a non-US exit (ops cost + key
     handling; gated).
   - **Offline**: `https://data.binance.vision` bulk dumps — not for live panel.
4. **Decision for M7-6 deploy issue:** default **vision endpoints on existing VPS**;
   escalate to non-US sidecar only if vision rate limits or stream gaps show up in soak.

## BSC chain parameters

| Item | Value | Notes |
|------|-------|-------|
| Chain id | **56** | |
| Block time (live sample) | **~0.45 s avg** over 30 consecutive blocks (RPC timestamps are 1 s resolution → 0/1 s steps; treat as coarse) | Post-Maxwell/Fermi era (public posts: Maxwell 0.75s, Fermi ~0.45s). vs Mantle ~2s → on the order of **~4×** more blocks/hour if polling every block — re-measure on keyed RPC before locking collector SLO. |
| Public RPC | `https://bsc-dataseed.binance.org`, `bsc-dataseed1…4`, `https://1rpc.io/bnb` | Ankr public needs API key (Unauthorized). Prefer keyed (QuickNode/Ankr paid) for collector SLO. |
| Multicall3 | `0xcA11bde05977b3631167028862bE2a173976CA11` | Code present (~3.8 KB). |
| PCS V3 factory | `0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865` | Official PCS addresses. |
| USDT | `0x55d398326f99059fF775485246999027B3197955` | 18 decimals on BSC. |

### Swap event signatures (phase-1 pit applies)

PCS V3 (and Agni) use the **protocolFees-extended** Swap event — already handled in-repo:

```text
# PCS / Agni (topic0 used by real PCS V3 pools)
Swap(address,address,int256,int256,uint160,uint128,int24,uint128,uint128)
topic0 = 0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83

# Vanilla Uniswap V3 (keep as fallback)
Swap(address,address,int256,int256,uint160,uint128,int24)
topic0 = 0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67
```

Code: `src/monitor/fluxion/abi.py` (`TOPIC0_V3_SWAP_PCS`), phase-1 `src/mba/m1_scan_events.py`.
**M7 BSC collector should subscribe to the PCS topic0 first**, same dual-accept pattern.

**Live log decode:** public bsc-dataseed `eth_getLogs` returned `limit exceeded` for the
TSLAB pool during inventory; topic0 above is therefore **lineage-asserted** (PCS V3 +
in-repo Agni constants), not re-decoded from a fresh bStock swap receipt in this PR.
M7 collector work must re-verify on a keyed RPC with one live swap before trusting
decoding.

Fee tiers observed on bStock pools: **100 / 2500 / 10000** (PCS includes 2500; not only
Uni’s 500/3000).

## Fees, deposit / withdraw (闭环)

| Item | Finding |
|------|---------|
| Binance spot taker | **0.10%** base schedule (VIP discounts / BNB burn reduce further — **ignore BNB discount** for paper edge floor, same spirit as DESIGN cost floors). Source: Binance fee schedule product standard; confirm live via signed `GET /api/v3/account` when keys exist off-US. |
| PCS pool fee | Per-pool `fee()` — top set mostly **2500 (25 bps)**; SPYB **100 (1 bp)**; MSFTB **10000 (100 bps)**. |
| Deposit / withdraw bStocks | **Open** on **BSC only** (FAQ). Network fee on withdraw; mins on cryptoFee page. Corporate-action windows may pause deposit/withdraw/convert while spot trading continues. |
| 闭环 realism | CEX⇄wallet on BSC is supported → paper + eventual live transfer arb is **mechanically possible** for eligible non-US users. US persons / restricted regions excluded by product terms (out of scope for infra). |

## Implications for later M7 tickets

| Ticket | Input from this note |
|--------|----------------------|
| M7-2 domain/config | **Landed WHI-771:** `config/markets/binance-pancake.yaml` + `monitor.markets`; costs/multiplier_semantics=multiply; collector scaffold under `collector.yaml` `markets.binance-pancake`. |
| M7 collectors | **Landed WHI-772:** `monitor/binance` + BSC `ChainPoller` (no RFQ); vision hosts; `pool_state_every_n_blocks: 2`; PCS Swap topic0 dual-accept; multiply `ui_multiplier` at journal write. Live `uiMultiplier` refresh loop still open. |
| M7 metrics | Multiplier: **multiply** Binance mid by UI mult for raw comparison; costs: 10 bps CEX + pool fee bps; no USDT/USDC basis. |
| M7 attribution | AMM-only; drop RFQ mechanism axis or hard-code `mechanism=amm`. |
| M7-6 deploy | Prefer vision endpoints on existing VPS; optional non-US sidecar if vision insufficient. |

## Appendix A — Full Binance spot bStock candidates (55)

Heuristic at 2026-08-02 (`exchangeInfo`, TRADING, quote=USDT, base ends with `B` or
`MUB`, minus crypto: BNB/DGB/TRB/CKB/SHIB/ARB/BB/YB/QNT/QNTB). **Not** an official
Binance “bStocks product” flag — re-check when listings change.

```
MUBUSDT, CRCLBUSDT, NVDABUSDT, SNDKBUSDT, TSLABUSDT, SPCXBUSDT, AMDBUSDT,
EWYBUSDT, INTCBUSDT, MSTRBUSDT, LITEBUSDT, METABUSDT, MSFTBUSDT, PLTRBUSDT,
QQQBUSDT, CBRSBUSDT, COINBUSDT, DRAMBUSDT, GLWBUSDT, GOOGLBUSDT, NBISBUSDT,
QCOMBUSDT, SOXLBUSDT, SPYBUSDT, WDCBUSDT, SKHYBUSDT, AAOIBUSDT, ARMBUSDT,
AVGOBUSDT, BABABUSDT, HOODBUSDT, IBMBUSDT, MRVLBUSDT, NOKBUSDT, TSMBUSDT,
RKLBBUSDT, AXTIBUSDT, CRWVBUSDT, INTWBUSDT, KORUBUSDT, MUUBUSDT, MVLLBUSDT,
ORCLBUSDT, SNXXBUSDT, TQQQBUSDT, AAPLBUSDT, AMATBUSDT, AMZNBUSDT, BEBUSDT,
DELLBUSDT, FLNCBUSDT, GSBUSDT, PYPLBUSDT, SMHBUSDT, SOXSBUSDT
```

**On-chain-verified Pancake USDT pool (any liq):** SPCXB, SKHYB, TSLAB, SPYB, NVDAB,
AAPLB, GOOGLB, MSFTB, INTCB, MUB, SOXLB, MUUB (12). **Top 10** = first 10 of that set
ranked by DexScreener liq (SOXLB #11, MUUB #12 / broken mid).

## Open risks / follow-ups

1. **Token address registry** — no single public machine-readable Binance “all bStock
   contracts” API found; 2026-07-29 announcement embeds bscscan links for that batch.
   Prefer announce + on-chain symbol match; re-inventory when listings change.
2. **QQQB-class DexScreener false positives** — always verify `token0/1`.
3. **uiMultiplier history** — no CEX websocket field; need event
   `UIMultiplierUpdated` log subscription for precise corporate-action timing.
4. **Vision WS soak** — upgrade works; long-run disconnect/rate limits not measured here.
5. **SPCXB** — SpaceX not a traditional public equity; treat as high-vol / special risk
   despite top liquidity. **Session calendar:** M3 NYSE open/closed segmentation does
   **not** apply cleanly — SPCXB (private) and SKHYB (KRX primary listing) need per-asset
   session rules in M7 metrics, not a hard copy of xStocks NYSE hours.
6. **PCS Swap topic0 on live bStock pool** — not re-decoded this PR (public RPC
   `eth_getLogs` limit); collector must confirm before production decode.
7. **V2 / StableSwap exhaustiveness** — re-inventory may want PCS V2 factory
   `getPair(token, USDT)` + StableSwap plain-AMM registry calls, not only DexScreener.
8. **ui_multiplier direction soak** — multiply formula is FAQ/BEP-677-derived; re-check
   CEX mid vs AMM raw across the next non-trivial dividend or split event.

## Evidence index

| Claim area | Primary link / artifact |
|------------|-------------------------|
| Product / 1:1 backing | https://www.binance.com/en/academy/articles/what-are-bstocks-a-guide-to-tokenized-stocks-on-binance |
| Multiplier FAQ | https://www.binance.com/en/support/faq/detail/f0c03cd6509a4085b4cce1636f16be38 |
| Deposit/withdraw BSC | https://www.binance.com/en/support/faq/detail/f0d41139fadc4790bf9a4c0c7bce2e88 |
| Intro listing | https://www.binance.com/en/support/announcement/detail/2c0c92ed15ac42d1b14bb1eac00d22bb |
| Add-10 contracts | https://www.binance.com/en/support/announcement/detail/fd3c0f17a7504eb5be1cb1911c6da0cd |
| Pancake Terminal | https://blog.pancakeswap.finance/articles/bstocks-on-pancakeswap · https://pancakeswap.finance/stocks |
| BEP-677 | https://github.com/bnb-chain/BEPs/blob/master/BEPs/BEP-677.md |
| Maxwell 0.75s | https://www.bnbchain.org/en/blog/bnb-chain-announces-maxwell-hardfork-bsc-moves-to-0-75-second-block-times |
| Fermi ~0.45s (context) | https://docs.bnbchain.org/announce/fermi-bsc/ (and secondary write-ups); live sample via `eth_getBlockByNumber` on bsc-dataseed 2026-08-02 |
| Geo probe | SSH `whi715-vps` / `107.175.234.202` 2026-08-02: `api.binance.com` → 451; `data-api.binance.vision` → 200 bookTicker; `data-stream.binance.vision` → WS 101; `stream.binance.com` → 451 |
| On-chain reads | `uiMultiplier()`, `fee()`, `slot0()`, `token0/1` via `https://bsc-dataseed.binance.org` |
| Liquidity | DexScreener `api.dexscreener.com/latest/dex/search?q=<BASE>` inventory-time |
| Spot universe | `GET https://api.binance.com/api/v3/exchangeInfo` (SG host) → Appendix A |
