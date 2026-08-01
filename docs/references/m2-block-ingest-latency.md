# Mantle block ingest latency & head_lag (WHI-749)

Research note closing the deferred item from WHI-743: measure
`block_ts → recv_ts` properly, compare `head_lag_blocks` / poll interval, and
fix defaults + M2 acceptance.

**Collected:** 2026-08-01 ~12:29–13:35 UTC on the research host (macOS), 8 AMM
pools from `config/pairs.yaml`, chain-only probe
(`python -m monitor.collector.latency_probe` — same `ChainPoller` path as the
collector, Bybit/RFQ loops skipped).

**Raw summaries:** `docs/references/probe-data/*.md` (markdown reports). Full
per-block JSON samples are local artifacts (large; optional re-run of the probe
regenerates them). Do not commit probe `*.log` (httpx may embed keyed RPC paths).

## Metric definition (confirmed)

```
latency_ms = max(0, recv_ts_ms - block_ts * 1000)
```

| Symbol | Meaning |
|--------|---------|
| `block_ts` | On-chain block timestamp (seconds) from `eth_getBlockByNumber` |
| `recv_ts_ms` | Local wall clock **after** all per-block RPC completes (getBlock + Multicall3 pool state + pool `getLogs` + optional swap receipts + LOP `getLogs`) — see `ChainPoller._process_block` |
| `poll_wait_ms` | `discovered_ms - block_ts*1000` — age of the block when getBlock is first attempted |
| `rpc_work_ms` | `recv_ts_ms - discovered_ms` — RPC + decode path only |

This matches daemon meta `last_block_ingest_latency_ms` (and WHI-731 wording
“块时间→入库”). **It is not** “RPC processing only” and **it includes**:

1. Local clock vs chain clock skew (host-dependent). **Explicit check** on this
   research host: `wall_s - block_ts` at tip was **~3–5 s** (wall ahead of
   Mantle) at two spot samples during the runs — not NTP-corrected. Deploy VPS
   skew may differ; re-check with
   `python -c` against `eth_getBlockByNumber` after deploy.
2. Time until the settled head is visible on the RPC LB.
3. Full multi-call work for 8 xStock pools (~1–2 s keyed P50).

So a single smoke `last_block_ingest_latency_ms ≈ 7–9 s` is **expected** under
`head_lag_blocks: 0` on a clock-ahead host; it is not evidence of a 7 s pure
RPC hang. The revised P95 &lt; 15 s SLO is a **freshness** bound that absorbs
typical host skew + lag=1 + RPC work; operators should still keep NTP sane so
skew does not dominate. Use rolling percentiles
(`block_ingest_latency_p50/p95/p99_ms` meta, or this probe) — never a single
meta sample — for SLOs.

Mantle block interval in-sample: **exactly 2 s** between consecutive blocks.

## Comparative results

Warmup = first 30 blocks (“startup”); remainder = “steady”. Nearest-rank
percentiles.

| Config | Duration | Samples | latency P50 / P95 (ms) | rpc_work P50 / P95 | not-found events (unique blocks) |
|--------|----------|---------|------------------------|--------------------|----------------------------------|
| **keyed, lag=0, poll=0.25** | 15.6 min | 414 | 7333 / **48799** | 1320 / 4328 | 9 (7 unique) + fat catch-up tail |
| **keyed, lag=1, poll=0.25 A** | 15.0 min | 451 | 8838 / **11383** | 1420 / 2164 | **0** |
| **keyed, lag=1, poll=0.25 B** | 15.0 min | 450 | 8150 / **12594** | 1118 / 2268 | ~30 events / **~18 unique** (transient; no skips) |
| keyed, lag=1, poll=0.10 | 10.0 min | 302 | 5967 / 7682 | 347 / 750 | 72 (39 unique) — worse |
| public, lag=1, poll=0.25 | 10.0 min | 299 | 12253 / **28494** | 1459 / 2815 | 0 in this window |
| keyed, lag=2, poll=0.25 | 10.0 min | 302 | 10739 / 13248 | 1351 / 2248 | **0** (higher freshness cost) |

Combined keyed lag=1 @ 0.25 s: **~30 min**, **901 samples**, latency P95 stays
~11–13 s (no multi-10s tail). Not-found is **not guaranteed zero** on the keyed
LB even at lag=1 — run B saw ~18 unique transient misses — but every block was
still ingested (zero holes in the block-number series); misses only flag
`gap=1` on the retry path. **lag=2** zeroed not-found in a 10 min window at
~+2 s extra P50; leave as operator override if gap noise matters more than
freshness.

### Startup vs steady

| Config | Window | n | latency P50 / P95 | notes |
|--------|--------|---|-------------------|-------|
| keyed lag=0 | startup | 30 | 7923 / 11622 | first 30 blocks |
| keyed lag=0 | steady | 384 | 7287 / **49219** | fat tail is **steady**, not warmup |
| keyed lag=1 A | startup | 30 | 8866 / 9819 | no cold-start spike |
| keyed lag=1 A | steady | 421 | 8823 / 11398 | same regime |

Startup is **not** the source of the 7–9 s smoke readings; lag=0 steady-state
median is already ~7 s, and the multi-10s P95 is a steady catch-up tail after
not-found stalls.

**Continuity note:** the “~30 min keyed lag=1” summary stitches two sequential
15 min probes (A then B), not one uninterrupted 30 min process. Gap rates
differ between halves (A: 0 not-found; B: ~18 unique) — annotation strategy
below applies; do not read A alone as a 30 min zero-gap proof.

### Decomposition (keyed lag=1 run A, all samples)

| Component | P50 (ms) | P95 (ms) | Role |
|-----------|----------|----------|------|
| poll_wait | 7430 | 9701 | skew (~3–5 s) + 1-block settle (~2 s) + tip visibility |
| rpc_work | 1420 | 2164 | actual collector work |
| **latency** | **8838** | **11383** | sum (order-preserving approx.) |

Rough budget on this host: `skew(5) + head_lag(2) + rpc(1.4) ≈ 8.4 s` ≈ observed P50.

### head_lag=0 pathology

Chasing tip (`head_lag_blocks: 0`) hits load-balanced read-your-writes
inconsistency: `eth_blockNumber` returns N, `eth_getBlockByNumber(N)` returns
null → gap `"block N not found"`, poller stalls on N, then catch-up processes
**stale** blocks. That creates the fat tail (P95 ~49 s, max ~89 s) even though
median stays ~7 s. Same root cause as phase-1 `HEAD_LAG_BLOCKS` (see README).

### Tighter poll (0.10 s) is not a free lunch

`block_poll_interval_s: 0.1` with lag=1 **lowered** median latency slightly but
produced **dozens** of not-found gaps (retry spam + more tip-adjacent races).
Keep **0.25 s**.

### Public RPC

With lag=1, public `rpc.mantle.xyz` had **zero** not-found in one 10 min window,
but freshness is worse (P95 ~28 s). **Keyed RPC is the acceptance baseline**;
public is degrade-mode only.

## Selected defaults

| Knob | New default | Rationale |
|------|-------------|-----------|
| `mantle.head_lag_blocks` | **1** | Eliminates block-not-found on keyed LB; +~2 s systematic lag beats multi-10 s tails |
| `mantle.block_poll_interval_s` | **0.25** (unchanged) | 0.10 increases gap noise without meaningful SLO win |

## Revised M2 latency acceptance (WHI-731 / DESIGN)

Original WHI-731 AC: “采集延迟（块时间→入库）P95 < 2s”.

**Unreachable** under the true metric definition when:

- host wall clock is multi-second ahead of chain timestamps, **and/or**
- `head_lag_blocks ≥ 1` (adds ≥1 Mantle block ≈ 2 s), **and/or**
- 8-pool Multicall + logs need ~1–2 s RTT budget.

Replace with (keyed Mantle RPC, steady state after warmup):

| Criterion | Target |
|-----------|--------|
| `block_ts → recv` latency P95 | **&lt; 15 s** |
| `rpc_work` P95 (processing) | **&lt; 3 s** |
| Skip-to-tip / non-contiguous block series | **0** under steady keyed poll at defaults |
| `block N not found` | May be non-zero (transient LB); must not produce multi-10s latency tails; see annotation strategy |
| Meta for ops | Rolling `block_ingest_latency_p50/p95/p99_ms` (+ `last_*`); never treat a single smoke `last_*` as the SLO |

Public-RPC-only deploys: no hard P95; expect worse freshness; still prefer
`head_lag_blocks: 1`.

## Gap annotation strategy

| Detail pattern | Meaning | Action |
|----------------|---------|--------|
| `block N not found` | LB read-your-writes; settled head not on this backend yet | **Expected occasionally** even at `head_lag_blocks: 1` on keyed LB. Poller **retries the same N** (does not skip). Each miss emits a `collector_gaps` row and sets `gap=1` until a successful block. **Not** a data hole if block numbers stay contiguous. Rate: prefer unique-block count over raw event count (retries at 0.25 s spam rows). |
| `lag=… skipping to tip` | Fell behind `max_catchup_blocks` | Real continuity break — investigate RPC stalls / process pauses |
| `poll error: …` | Transport / RPC exception | Check keyed URL, rate limits |

**30 min zero not-found is not a hard guarantee** on multi-backend keyed RPC;
acceptance is: (1) no skip-to-tip under load, (2) latency P95 stays &lt; 15 s
without the lag=0 catch-up tail, (3) operators read `gap=1` as “tip visibility
glitch”, not “lost block.” Raise `head_lag_blocks` to 2 only if not-found rate
on the deploy host is operationally noisy.

## Tooling

```bash
# Live chain-only probe (default: collector.yaml + MANTLE_RPC_URL)
uv run python -m monitor.collector.latency_probe --duration-s 900 --head-lag-blocks 1

# Offline from an existing journal
uv run python -m monitor.collector.latency_probe --analyze-db data/monitor.db
```

Daemon writes rolling percentiles into SQLite `meta` keys
`block_ingest_latency_{p50,p95,p99}_ms` and `block_ingest_latency_n` (window 256
blocks).

## Conclusion

1. Metric口径正确；7–9 s smoke values are **steady-state order of magnitude**,
   not pure startup glitches — dominated by **clock skew + tip visibility**, not
   a multi-second single RPC hang.
2. **`head_lag_blocks: 1`** is the correct default: removes the lag=0
   catch-up P95 tail (~49 s → ~12 s) even though transient not-found can still
   appear on the keyed LB (run B). Continuity held (no block skips).
3. Original **P95 &lt; 2 s** AC is revised to **keyed P95 &lt; 15 s** (freshness)
   plus **rpc_work P95 &lt; 3 s** (pipeline budget); not-found zero is **not**
   a hard 30 min guarantee — annotation strategy applies.
4. Ship config + DESIGN updates with this note; re-check on the VPS after deploy
   (skew may differ).
