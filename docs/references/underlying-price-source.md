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
| SPCX | both | **private** SpaceX — no public equity print |

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
| **SPCX (SpaceX)** | None | None | None | None | None | None (not public) |
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

## Decision

**Primary: Pyth Network Hermes HTTP** for all US equities + SPY.

| Choice | Why |
|--------|-----|
| Hermes over on-chain Pyth/Chainlink | Same oracle-grade numbers, **web2 latency and $0 RPC**; no Mantle/BSC feed deployment dependency |
| Hermes over paid web2 | Covers every liquid US name in inventory **without keys**; free tier rate limits never gate a 15–60s poll of ~14 IDs |
| Hybrid allowed | **Yahoo chart fallback** only for tickers Pyth cannot serve (**SKHY US ADR** today); optional — disable via config |
| SPCX | **No public underlying.** Collector skips; journal has no rows; UI (WHI-779) must show “n/a / private” |

**Rejected as primary:**

- **Chainlink/RedStone on-chain** — higher cost, weaker confirmed equity coverage on Mantle/BSC for this set; no advantage vs Hermes for a read-only panel.
- **Polygon / Finnhub / Alpha Vantage as primary** — need keys, free tiers are delayed or tight; license/rate work is pure overhead when Hermes covers US.
- **Yahoo as primary** — ToS / unofficial; fine as **narrow gap-fill**, not the default path.

### License conclusion

| Source | Use here |
|--------|----------|
| **Pyth Hermes** | Allowed for private monitoring and panel display. No API key. Pin feed IDs in config; do not scrape arbitrary Hermes endpoints beyond price reads. |
| **Yahoo chart API** | Unofficial, no redistributable commercial license assumed. Used **only** as optional SKHY ADR (and similar) gap-fill inside this private monitor. If compliance tightens, set `yahoo_fallback: false` and leave SKHY empty. |
| **SPCX** | No licensed public tape exists; do not synthesize prices. |

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
| `source` | `pyth_hermes` / `yahoo` / `yahoo+pyth_fx` (SKHY ADR → `yahoo`) |
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
```

Config: `config/underlying.yaml` + `underlying:` block in `config/collector.yaml`.
