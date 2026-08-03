# Deploy: web panel + read-only API (WHI-757)

Target: **whi715-vps** (1GB RAM, nginx already running). All data stays on the
box. Node is **not** a runtime dependency on the VPS.

## Layout

| Path | Role |
|------|------|
| `/opt/xstocks/app` | Git checkout / rsynced sources + `.venv` |
| `/opt/xstocks/app/data/monitor-{market}.db` | Per-market collector journal (WAL; API reads one market — ADR-0001) |
| `/opt/xstocks/www` | Next.js static export (`web/out`) |
| `xstocks-api.service` | `python -m monitor.api --market bybit-fluxion` (uvicorn, 1 process) |
| `xstocks-collector@.service` | Template: `xstocks-collector@bybit-fluxion` → `--market %i` |
| nginx | Serves `/opt/xstocks/www` + reverse-proxies `/api/` → `127.0.0.1:8000` |

## One-time VPS setup

```bash
# 1) User + dirs
sudo useradd -r -m -d /opt/xstocks -s /bin/bash xstocks || true
sudo mkdir -p /opt/xstocks/{app,www,app/data}
sudo chown -R xstocks:xstocks /opt/xstocks

# 2) Python env (as xstocks)
sudo -u xstocks -H bash -lc '
  curl -LsSf https://astral.sh/uv/install.sh | sh
  cd /opt/xstocks/app
  # after first rsync of sources:
  uv sync --no-dev
'

# 3) systemd (API + per-market collectors — WHI-825)
# Template unit Restart=always; enable every market you run so none sits
# unsupervised (kill -9 / watchdog non-zero exit both revive the process).
sudo cp /opt/xstocks/app/deploy/xstocks-api.service /etc/systemd/system/
sudo cp /opt/xstocks/app/deploy/xstocks-collector@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xstocks-collector@bybit-fluxion
sudo systemctl enable --now xstocks-collector@binance-pancake
sudo systemctl enable --now xstocks-api
# Verify both are wanted + restarting:
#   sudo systemctl --no-pager status 'xstocks-collector@*'

# 4) nginx
sudo cp /opt/xstocks/app/deploy/nginx-xstocks.conf /etc/nginx/sites-available/xstocks
sudo ln -sf /etc/nginx/sites-available/xstocks /etc/nginx/sites-enabled/xstocks
# edit server_name; disable any conflicting default site if needed
sudo nginx -t && sudo systemctl reload nginx
```

Collector is market-scoped (`--market bybit-fluxion` by default). It writes
`data/monitor-bybit-fluxion.db` (see ADR-0001). Migrate a legacy journal once:

```bash
mv data/monitor.db data/monitor-bybit-fluxion.db
```

The API and collector for a market must agree on the same journal path
(`config/api.yaml` / `config/collector.yaml` `markets.<id>.sqlite_path`).

### Restart collectors after collector code ships

`./scripts/deploy-web.sh` restarts **only** `xstocks-api`. Long-running
`xstocks-collector@*` units keep the in-memory binary from process start — they
do **not** pick up new collector features (e.g. WHI-778 `underlying` poller)
until restarted. After any deploy that changes `src/monitor/collector`,
`src/monitor/underlying`, `src/monitor/bybit`, `src/monitor/binance`,
`src/monitor/fluxion`, or `config/collector.yaml` / `config/underlying.yaml`:

```bash
# On the VPS — restart every market instance you run
sudo systemctl restart xstocks-collector@bybit-fluxion
sudo systemctl restart xstocks-collector@binance-pancake   # if enabled
sudo systemctl --no-pager status 'xstocks-collector@*'

# Confirm underlying is alive on **both** markets (WHI-788 dual-market smoke):
for m in bybit-fluxion binance-pancake; do
  echo "=== $m ==="
  sqlite3 /opt/xstocks/app/data/monitor-$m.db \
    "SELECT key, value FROM meta WHERE key LIKE 'underlying%' ORDER BY key;"
  sqlite3 /opt/xstocks/app/data/monitor-$m.db \
    "SELECT ticker, COUNT(*), MAX(as_of_ms) FROM underlying_prices GROUP BY ticker;"
done
# While the collector is up: underlying_status=running, underlying_last_poll_ms
# advancing, underlying_last_n > 0 after the first successful Hermes poll.
# Public US tickers (AAPL, NVDA, TSLA, …) should have rows; SPCX/SKHY via Yahoo
# gap-fill (source=yahoo). uncovered_coverage_mismatches on /api/health should
# be empty unless a ticker is still marked uncovered but a public tape exists.
# Smoke without the full collector: `uv run python -m monitor.underlying \
#   --tickers AAPL,TSLA,NVDA,SPCX,SKHY` (Hermes + Yahoo; no journal write).
# Uncovered guardrail only: `uv run python -m monitor.underlying --probe-uncovered`
#
# underlying_status values:
#   running      — poll loop active
#   stopped      — loop exited (process may still be up for other feeds)
#   disabled     — collector.yaml underlying.enabled=false
#   config_error — underlying.yaml failed to load (see underlying_last_error)
#   no_tickers   — inventory produced an empty ticker set
# underlying_last_poll_ms advances on every attempt (incl. empty/error);
# underlying_last_n is the tick count of the last attempt; last_error holds
# the most recent poll exception (not cleared by empty successful polls).
# WHI-787 / WHI-794 coverage guardrail meta (also on GET /api/health):
#   underlying_uncovered_mismatches   — JSON [{ticker, sources, detail}]
#   underlying_uncovered_probe_errors — JSON [{ticker, source, error}]
#   underlying_uncovered_probe_ms     — last probe attempt (ms)
#   underlying_unpublished_feeds      — JSON never-published Hermes pins (WHI-794)
```

Local dogfood: restart each collector process after `git pull` / feature merge
(`./scripts/dev-web.sh restart` starts **both** markets by default — WHI-825;
or kill + re-run `python -m monitor.collector --market …` per market).

### Watchdog + downtime gaps (WHI-825)

- In-process watchdog (`config/collector.yaml` `watchdog:`): if no **tick-table**
  writes for `reconnect_idle_s` → force CEX WS reconnect; if still silent for
  `exit_idle_s` → non-zero exit. Meta `collector_heartbeat_ms` still advances so
  `/api/health` can show **feed quiet** (process alive) vs **feed down** (process
  dead). Quiet closed-session bookTicker alone does **not** trip the watchdog.
- On restart, `[last tick write, first new write]` is recorded as
  `collector_gaps.source=collector_down` (skip &lt; `min_down_gap_ms`). EdgeStats
  zero-weights that interval so cumulative P50/P95 are not polluted.
- Panel: `feed_state` is `ok | feed_down | feed_quiet | gap` with `recovery_hint`.
## Redeploy (one command from a laptop)

```bash
# Build Next static export locally, rsync www + API sources, restart unit
./scripts/deploy-web.sh xstocks@whi715-vps

# Variants
./scripts/deploy-web.sh xstocks@host --skip-build   # reuse web/out
./scripts/deploy-web.sh xstocks@host --www-only     # static only
./scripts/deploy-web.sh xstocks@host --api-only     # Python only
```

**Collector code is rsynced with the API tree, but units are not restarted.**
See [Restart collectors after collector code ships](#restart-collectors-after-collector-code-ships).

## Local dogfood

```bash
# Terminal A — API (needs a real or fixture journal)
uv run python -m monitor.api --reload

# Terminal B — Next dev (proxy-less: point at API origin)
cd web && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 npm run dev
```

For static export parity: `cd web && npm run build && npx serve out` and set
CORS in `config/api.yaml` `cors_origins` for the serve origin.

## Memory report (acceptance)

On the VPS after a warm collector + API + nginx:

```bash
ps -o rss=,comm= -C nginx,python,uvicorn 2>/dev/null
# or:
systemctl status xstocks-api --no-pager | head
ps aux | egrep 'nginx|monitor\.(api|collector)' | grep -v egrep
free -h
```

Record RSS for collector / API / nginx into the PR or a short note under
`docs/references/` when first measured on whi715-vps.

## OpenAPI

With the API up: `http://<host>/docs` and `http://<host>/openapi.json`.
