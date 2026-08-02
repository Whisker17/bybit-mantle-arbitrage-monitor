# Underlying equity price source (WHI-778)

Companion to `config/underlying.yaml` and `monitor/underlying/`.
Research + decision date: **2026-08-02**. SKHY product semantics corrected
**2026-08-02 (WHI-785)** — US ADR, not KRX.

## Goal

Answer: **tokenized mid vs the real underlying equity** (premium bps). This issue
selects a source and lands collection into journal table `underlying_prices`.
Display wiring is **WHI-779** (out of scope).

Inventory underlyings (union of Bybit xStocks + Binance bStocks top sets):

| Ticker | Markets | Notes |
|--------|---------|--------|
| AAPL, GOOGL, NVDA, TSLA | both | liquid US names |
| CRCL, HOOD, META, AMZN, COIN, MCD | bybit-fluxion | US equities |
| SPY, MSFT, INTC, MU | binance-pancake | US / ETF |
| SKHY (Nasdaq ADR) | binance-pancake | SK hynix **US ADR** (`SKHY`); USD tape for bStock premium |
| SPCX | both | SpaceX — **Nasdaq SPCX** since 2026-06-12 IPO; Yahoo gap-fill (no Hermes) |

**WHI-785 correction:** WHI-778 originally mapped SKHY → KRX `000660` / Yahoo
`000660.KS` + KRW→USD FX. That was wrong for bStock product semantics: Binance
`SKHYB` is priced against the **US ADR**, not the Korean common. Premium columns
are meaningless across venue/currency/session mismatch. Authoritative reference
is Nasdaq `SKHY` (permanent ticker as of 2026-07-13; prior SKHYV / OTC HXSCL).

## Decision matrix

| Dimension | Pyth Hermes HTTP | Chainlink / RedStone (on-chain) | Polygon.io | Finnhub | Alpha Vantage | Yahoo chart (unofficial) |
|-----------|------------------|----------------------------------|------------|---------|---------------|--------------------------|
| **US coverage (our 14 liquid names)** | **All 14** `Equity.US.{T}/USD` | Equity feeds sparse; **none** confirmed on Mantle/BSC for our set without paid nets | Broad US | Broad US | Broad US | Broad US |
| **SKHY (Nasdaq ADR)** | **No** `Equity.US.SKHY/USD` on Hermes (probe 2026-08-02). Stale KR feed `Equity.KR.000660/KRW` is **not** the product underlying | Unlikely free on our chains | Paid | Free tier | Free tier | Live **USD** ADR quote (`SKHY`) |
| **SPCX (SpaceX)** | **No** `Equity.US.SPCX/USD` (probe 2026-08-02) | None | Paid likely | Free tier | Free tier | Live **USD** Nasdaq quote (`SPCX`) |
| **Update cadence** | ~5s publish interval on RTH schedule | On-chain heartbeat (varies) | Realtime on paid | Free ~60/min | Free very tight | ~1m bars / last trade |
| **Closed-session semantics** | RTH feed freezes `publish_time` at last close — usable as **close** | Last on-chain update | Explicit session fields | `t` timestamp | Daily bars | `marketState` + last trade |
| **Pre/post** | Separate `.PRE`/`.POST`/`.ON` feeds marked **DEPRECATED** | N/A | Yes (paid) | Limited free | No | Pre/post fields when open |
| **Auth / cost** | **Free, no key** | RPC cost + feed availability | API key, free delayed | Free key | Free key | No key (ToS grey) |
| **License for private monitor** | Oracle public data via Hermes — suitable for display/reference | Oracle ToS; still need RPC | Free tier ToS for non-commercial often OK | Free tier OK for personal | Strict free-tier limits | **Unofficial**; not for redistribution |
| **Access path** | `GET hermes.pyth.network/v2/...` | eth_call per feed | REST | REST | REST | `query1.finance.yahoo.com` |

### Probe notes (2026-08-02, weekend / US closed)

- Hermes `latest` for AAPL/TSLA/… returned `publish_time` ≈ **2026-07-31 20:00 UTC** (prior Friday RTH close) — correct **close** freeze, not a fake Saturday live print.
- Price scale: `price * 10^expo` (e.g. AAPL ≈ 309.85 USD).
- Hermes has **no** `Equity.US.SKHY/USD` (or SKHY/Hynix query hit) as of 2026-08-02 → Yahoo ADR path required until Pyth lists it.
- Yahoo `SKHY` (NMS, EQUITY) ≈ **143.73 USD** (probe 2026-08-02) — correct ADR level for premium vs bStock USD.
- *(Historical WHI-778 mistake)* Yahoo `000660.KS` ~1.72M KRW + Pyth KR feed stale publish_time — do **not** use for SKHYB premium.
- **SPCX is public.** SpaceX listed on Nasdaq as `SPCX` on **2026-06-12** (IPO ~$135). Yahoo chart returns
  `symbol=SPCX, fullExchangeName=NasdaqGS, shortName="Space Exploration Technologies",
  regularMarketPrice≈108.37` (probe 2026-08-02). Hermes `/v2/price_feeds?query=SPCX|SPACEX`
  returns `[]` — no Pyth equity feed yet. WHI-778 incorrectly marked SPCX
  `uncovered: true` as "private"; **WHI-787** corrects that to Yahoo gap-fill.

## Decision

**Primary: Pyth Network Hermes HTTP** for all US equities + SPY.

| Choice | Why |
|--------|-----|
| Hermes over on-chain Pyth/Chainlink | Same oracle-grade numbers, **web2 latency and $0 RPC**; no Mantle/BSC feed deployment dependency |
| Hermes over paid web2 | Covers every liquid US name in inventory **without keys**; free tier rate limits never gate a 15–60s poll of ~14 IDs |
| Hybrid allowed | **Yahoo chart fallback** for tickers Pyth cannot serve (**SKHY US ADR**, **SPCX** today); optional — disable via config |
| SPCX | **Public Nasdaq equity.** No Hermes feed → Yahoo `SPCX` gap-fill (same path as SKHY). UI shows real premium vs Und |

**Rejected as primary:**

- **Chainlink/RedStone on-chain** — higher cost, weaker confirmed equity coverage on Mantle/BSC for this set; no advantage vs Hermes for a read-only panel.
- **Polygon / Finnhub / Alpha Vantage as primary** — need keys, free tiers are delayed or tight; license/rate work is pure overhead when Hermes covers US.
- **Yahoo as primary** — ToS / unofficial; fine as **narrow gap-fill**, not the default path.

### License conclusion

| Source | Use here |
|--------|----------|
| **Pyth Hermes** | Allowed for private monitoring and panel display. No API key. Pin feed IDs in config; do not scrape arbitrary Hermes endpoints beyond price reads. |
| **Yahoo chart API** | Unofficial, no redistributable commercial license assumed. Used **only** as optional gap-fill (SKHY ADR, SPCX, …) inside this private monitor. If compliance tightens, set `yahoo_fallback: false` and leave those tickers empty. |
| **SPCX** | Public Nasdaq tape via Yahoo (Hermes gap). Do **not** mark `uncovered` again without re-probing both sources. |

## Session / corporate-action semantics (implementation contract)

1. **`price_type`** ∈ `{live, pre, post, close, stale}`:
   - **live** — US RTH open *and* `as_of` within freshness window.
   - **pre / post** — **only** when the source explicitly signals extended hours
     (Yahoo `marketState`). Never invent pre/post from wall-clock alone: Pyth’s
     RTH feed freezes `publish_time` at the last close, so weekday 16:00–20:00 ET
     would otherwise stamp Friday’s close as `post`.
   - **close** — outside RTH (or holiday/weekend) without a source pre/post hint;
     **must not** be treated as live premium without the label.
   - **stale** — RTH open but `as_of` older than `stale_after_open_ms`, or absolute
     age > `stale_after_abs_ms` (catches dead KR feeds).
2. **`as_of_ms`** = source publish/trade time (Pyth `publish_time`), never wall-clock alone.
3. **Split days / multiplier jumps** — tokenized `xstockMultiplier` / `uiMultiplier`
   and underlying as-of must be compared at aligned times. Collection marks
   **age-based stale** only; jump-vs-prior detection for corporate-action days is
   deferred to WHI-779 (needs both legs). Prefer “stale” over a silent premium spike.
4. **Poll cadence** — open `open_poll_interval_s` (default 30s), closed
   `closed_poll_interval_s` (default 300s). Single batched Hermes request for all
   feed IDs. Rows dedup on `(ticker, as_of_ms, source)` so frozen closes do not
   flood the journal.
5. **Keys** — none required for Pyth. Optional future keys stay in `.env`; missing
   keys must not crash the collector (graceful empty column).
6. **Dual-market write** — each collector process writes the underlyings its
   inventory needs into **its** per-market SQLite (ADR-0001). Shared *source +
   ticker map*, not a single shared DB; overlapping tickers may be polled twice
   (Hermes free batch, acceptable).

## Schema

Table `underlying_prices` (schema v5), dual-market shared **by ticker** (not `pair_id`):

| Column | Meaning |
|--------|---------|
| `ticker` | Canonical underlying id (`AAPL`, `MU`, `SKHY`, …) |
| `price` | Decimal string |
| `currency` | `USD` (SKHY ADR is native USD via Yahoo; optional KRW→USD via Pyth `FX.USD/KRW` only if a future Yahoo KR listing is configured) |
| `price_type` | `live` / `pre` / `post` / `close` / `stale` |
| `as_of_ms` | Source time |
| `recv_ts_ms` | Collector receive time |
| `source` | `pyth_hermes` / `yahoo` / `yahoo+pyth_fx` (SKHY ADR / SPCX → `yahoo`) |
| `feed_id` | Pyth price feed id when applicable |
| `gap` | Post-error flag |

Each market journal (`data/monitor-{market}.db`) stores the **union of tickers that market needs** (collector filters by inventory). Shared names (AAPL, NVDA, …) are written independently into each process’s DB — ADR-0001 keeps DBs separate; the *source and ticker map* are shared.

Pair → ticker stripping: `AAPLx`/`AAPLB` → `AAPL`, `MUB` → `MU`, `SKHYB` → `SKHY`, `SPCXB`/`SPCXx` → `SPCX` (see `monitor/underlying/tickers.py`).

## Acceptance (issue)

- [x] Decision matrix + license in this note
- [x] Collector writes `underlying_prices` for every **public** inventory ticker
- [x] Closed session labels `close` (not live); pre/post only with source hint
- [x] Local Hermes + Yahoo smoke: `uv run python -m monitor.underlying` (all covered tickers)
- [ ] 30-minute live soak on VPS (ops) — run both collectors and confirm
  `SELECT ticker, COUNT(*), MAX(as_of_ms) FROM underlying_prices GROUP BY ticker`

## Ops

```bash
# Collector (underlying loop starts with the market daemon when enabled):
uv run python -m monitor.collector --market bybit-fluxion
uv run python -m monitor.collector --market binance-pancake

# One-shot smoke (no journal required for parse tests):
uv run python -m monitor.underlying
uv run python -m monitor.underlying --tickers SKHY
# expect source=yahoo, currency=USD, price ~1e2 (Nasdaq ADR), not KRW/FX path
```

Config: `config/underlying.yaml` + `underlying:` block in `config/collector.yaml`.

**WHI-785 / WHI-787 deploy:** `underlying.yaml` is loaded at collector start.
After shipping SPCX Yahoo wiring (or any ticker map change), **restart both**
`xstocks-collector@bybit-fluxion` and `@binance-pancake` so journals pick up the
new path. Historical empty SPCX columns fill on the next open/closed poll.

```sql
SELECT ticker, price, currency, source, as_of_ms
FROM underlying_prices WHERE ticker IN ('SKHY','SPCX') ORDER BY as_of_ms DESC LIMIT 10;
-- expect source like 'yahoo', price ~ADR / Nasdaq USD level
```

## Coverage audit (WHI-787, probe 2026-08-02)

Live re-check of **every** inventory underlying against Hermes + Yahoo. Config
must match “actual available source”. Yahoo `regularMarketPrice` from
`query1.finance.yahoo.com/v8/finance/chart/{T}` (weekend / US closed → last
print / prior close). Hermes via `/v2/price_feeds?query=` + pinned feed ids.

| Ticker | Hermes | Yahoo (USD) | Config (post-WHI-787) | Match? |
|--------|--------|-------------|------------------------|--------|
| AAPL | `Equity.US.AAPL/USD` pinned | 308.91 | Hermes primary | yes |
| CRCL | pinned | 62.61 | Hermes primary | yes |
| GOOGL | pinned | 356.13 | Hermes primary | yes |
| HOOD | pinned | 86.56 | Hermes primary | yes |
| META | pinned | 556.71 | Hermes primary | yes |
| NVDA | pinned | 200.75 | Hermes primary | yes |
| TSLA | pinned | 311.21 | Hermes primary | yes |
| AMZN | pinned | 271.58 | Hermes primary | yes |
| COIN | pinned | 146.26 | Hermes primary | yes |
| MCD | pinned | 270.64 | Hermes primary | yes |
| SPY | pinned | 747.03 | Hermes primary | yes |
| MSFT | pinned | 464.72 | Hermes primary | yes |
| INTC | pinned | 90.2 | Hermes primary | yes |
| MU | pinned | 823.03 | Hermes primary | yes |
| SKHY | no (`query=SKHY` → `[]`) | 143.73 (Nasdaq ADR) | `prefer_yahoo` + `yahoo_symbol: SKHY` | yes |
| SPCX | no (`query=SPCX\|SPACEX` → `[]`) | 108.37 (NasdaqGS) | `prefer_yahoo` + `yahoo_symbol: SPCX` | yes (fixed) |
| *(none)* | — | — | `uncovered: true` | n/a — no uncovered remain |

**Premium recompute (both markets share ticker `SPCX` via `SPCXx` / `SPCXB`):**

* Bybit xStock: de-multiplied CEX mid ≈ **108.73** vs Yahoo **108.37** →
  ~**+33 bps** (`premium_bps = (mid/und − 1)×10⁴`). Mapping correct.
* Binance bStock: journal mid is in raw/`ui_multiplier` space; premium uses
  `equity_equivalent_mid = comparable / ui_multiplier` then the same bps formula
  against the same Yahoo SPCX print. After collector restart both markets write
  `underlying_prices` rows for `SPCX` (`source=yahoo`).

## Uncovered guardrail (WHI-787)

Bug class: a one-time “private / no feed” human judgment freezes in
`config/underlying.yaml` and never gets revisited.

1. **Collection path** — tickers with `uncovered: true` are still skipped by the
   price poller (no fake prints). UI empty reason remains `private` for any
   future truly-uncovered name.
2. **Periodic probe** — every `uncovered_probe_interval_s` (YAML required, e.g.
   3600s) the collector runs `UncoveredCoverageProbe` against Yahoo chart +
   Hermes `price_feeds` for each uncovered ticker. On a hit: **WARN** log +
   journal meta. Standalone: `uv run python -m monitor.underlying --probe-uncovered`.
3. **Journal meta** (also listed in `deploy/README.md`):
   * `underlying_uncovered_mismatches` — JSON list of `{ticker, sources, detail}`
   * `underlying_uncovered_probe_errors` — JSON list of `{ticker, source, error}`
     (total outage ≠ “all clear”)
   * `underlying_uncovered_probe_ms` — last probe attempt wall time
4. **Health** — `GET /api/health` exposes `uncovered_coverage_mismatches`,
   `uncovered_coverage_probe_errors`, `uncovered_coverage_probe_ms`. Advisory
   only — does **not** flip `ok`.
5. **Empty uncovered list** — probe is a no-op (no network). After WHI-787 the
   checked-in map has zero uncovered; the path stays for the next private or
   pre-IPO name.
6. **When the probe does not run** — if `collector.yaml` `underlying.enabled`
   is false, or inventory yields no tickers, the collector never constructs the
   probe (same gate as the price poller). Use
   `python -m monitor.underlying --probe-uncovered` for a one-shot check, or
   re-enable the underlying loop.
