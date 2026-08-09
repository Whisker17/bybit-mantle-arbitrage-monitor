# WHI-974: US-host upstream gaps (Bybit REST 403 + Fluxion RFQ 451)

Measured on RackNerd US/California AS3635 hosts
(`arb-bot-vps` 107.173.123.182, `whi715-vps` 107.175.234.202) after the
2026-08-09 deploy. Companion product decision for the panel — not a VPS move.

## Owner decisions (locked)

1. Overview **CEX Vol** is **exchange-reported** 24h turnover only. When REST is
   unavailable the cell is blank with an explicit `geo_blocked` reason — never a
   bare dash, never a journal-derived fill-in.
2. The **journal-derived** CEX figure stays on the **pair detail**
   `volume_compare` panel (partial / truncated). It must not enter the overview
   column or `volume_ratio`.

## Bybit REST 403 — geo block on every public REST host

| Host | Result from AS3635 |
|------|--------------------|
| `api.bybit.com` | HTTP **403** |
| `api.bytick.com` (official alternate) | **403** |
| `api.bybit.nl` | **403** |
| `api-testnet.bybit.com` | **403** |
| `stream.bybit.com` (WS) | **reachable** (HTTP 400 on bare GET; WS upgrade works) |

Observed collector behaviour:

* `bybit_book` / `bybit_depth` / `bybit_trades` populate normally (WS path).
* 60s REST poll for `cex_volume_24h` fails every cycle → **0** journal rows on
  `bybit-fluxion` while `binance-pancake` keeps writing (vision hosts; WHI-770).

There is **no reachable REST alternate** from this ASN. Mitigation is not
"find another endpoint" — it is visibility: quiet the log spam, stamp meta
(`cex_volume_status=geo_blocked` + host + first/last seen), and render
`geo_blocked` on the overview.

Binance treatment for comparison: WHI-770 switched defaults to
`data-api.binance.vision` / `data-stream.binance.vision` after measuring 451 on
`api.binance.com`. Bybit has no equivalent public vision path.

## Fluxion RFQ 451 — not a blanket geo block

```
POST https://fluxion.network/api/limit-order/quote → intermittent HTTP 451
```

* Logs showed hundreds of 451s per few minutes (primary URL).
* Journal historically held only `http_status` 200 / 204 — **451 rows were not
  persisted** when the Railway proxy failover returned a productive response.
* `/api/pairs` still showed two-sided RFQ mids (proxy path works). A bare
  `curl -X POST -d '{}'` returns 451 from the rack hosts and 400 from a
  residential line, so the IP range is implicated, but intermittent 200s from
  the **same** collector process rule out a permanent block.

### Characterisation (before any mitigation)

| Hypothesis | Evidence | Verdict |
|------------|----------|---------|
| Permanent geo block of Fluxion | Collector still gets 200s; panel has quotes | **Rejected** |
| Rate limit (429-shaped) | Status is **451**, evenly spread per pair×leg, not bursty 429 | Unlikely as classic rate-limit; may be edge policy |
| Request-shape dependent | Empty body → 451 on rack, 400 residential; valid EXACT_INPUT often 200 via proxy | Partial — bad shape always fails; good shape still 451 on primary |
| Edge-inconsistent primary vs proxy | Primary `fluxion.network` 451s; Railway proxy often 200/204 | **Supported** — failover hides primary loss |

**Do not** add a proxy of our own or a retry storm until a further probe isolates
rate vs policy. WHI-974 only makes failures countable: every non-200/204 HTTP
attempt leaves a `fluxion_rfq_quotes` row; `/api/health` exposes
`rfq_error_rate` over a 15m window. Availability rates must use the reachable
denominator (200+204), not total rows including errors.

## Panel behaviour after the fix

| Surface | Behaviour |
|---------|-----------|
| Overview CEX Vol | `geo_blocked` label when meta says so; else dash until first poll |
| Overview CEX/DEX ratio | Same reason when blank; never computed from journal |
| Detail `volume_compare` | Journal session split still shown, marked truncated/partial |
| RFQ journal | Error statuses persisted (incl. intermediate failover failures) |
| `/api/health` | `rfq_error_rate`, `rfq_http_status_counts`, `cex_volume_status` / host |
| Logs | 403 geo-block: one WARNING on transition, quiet until recovery |

## Out of scope (explicit)

* Promoting journal volume into overview / ratio
* Moving the VPS off US AS3635 (WHI-775)
* binance-pancake (unaffected)
* RFQ proxy/backoff productisation (needs further characterisation)

## References

* WHI-770 Binance vision hosts
* WHI-777 CEX/DEX volume columns
* WHI-753 closed-session RFQ
* `config/markets/bybit-fluxion.yaml` RFQ URLs
* `monitor.cex_volume.poller` / `monitor.fluxion.rfq`
