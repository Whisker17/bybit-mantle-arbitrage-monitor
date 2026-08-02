# ADR-0001: Per-market SQLite journals

* Status: Accepted
* Date: 2026-08-02
* Issue: WHI-771 (M7-2 multi-market domain model)

## Context

M7 adds a second venue pair (Binance ⇄ PancakeSwap bStocks) beside the existing
Bybit ⇄ Fluxion xStocks panel. Two storage shapes were considered:

1. **Single SQLite file** with a `market` column on every table.
2. **One SQLite file per market** (`data/monitor-{market_id}.db`).

Retention (WHI-751), disk waterlines, collector restarts, and read-only API
aggregation all interact with this choice.

## Decision

**One journal file per market.** Path convention:

```text
data/monitor-{market_id}.db
```

Examples:

* `data/monitor-bybit-fluxion.db` — default / live today
* `data/monitor-binance-pancake.db` — M7-3 collector target

The pre-M7-2 path `data/monitor.db` is a **legacy alias** for
`bybit-fluxion` only: if the configured per-market file is missing but the
legacy file exists, loaders fall back and log a warning. Operators should
rename once:

```bash
mv data/monitor.db data/monitor-bybit-fluxion.db
```

Cross-market aggregation (overview of both markets) is an **API / Web
concern** (M7-5), not a shared write path.

## Consequences

### Positive

* **Fault isolation** — a stuck Mantle or BSC RPC, or a corrupt WAL on one
  chain, cannot block the other market's collector or retention job.
* **Independent retention / disk waterlines** — WHI-751 policy runs per file;
  emergency TTL acceleration or pause-book on critical free space applies to
  the market that is filling the disk.
* **Independent process lifecycle** — systemd can use
  `xstocks-collector@{market}.service` and restart one market without
  touching the other.
* **Schema simplicity** — existing tables stay market-free; no backfill of a
  `market` column into historical rows.

### Negative / trade-offs

* API and Web must open **N** readers (or switch journal) for multi-market
  views (M7-5).
* Ops must manage N journal paths and N retention schedules (mitigated by
  shared `retention:` defaults in `config/collector.yaml` with optional
  per-market override).
* Disk accounting is per-file; a single global free-space probe still works
  (parent of each sqlite path).

## Rejected alternative

**Single DB + `market` column** was rejected because it couples two
independent feeds into one WAL, complicates retention batching (different
TTL needs per venue), and forces every query path to remember a market
filter. Isolation benefits of separate processes would be largely lost.

## References

* Linear WHI-771, WHI-751
* `config/collector.yaml` `markets:` sections
* `monitor.markets` assembly package
