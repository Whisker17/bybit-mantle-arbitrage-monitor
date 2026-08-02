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

# 3) systemd (API + default-market collector)
sudo cp /opt/xstocks/app/deploy/xstocks-api.service /etc/systemd/system/
sudo cp /opt/xstocks/app/deploy/xstocks-collector@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xstocks-collector@bybit-fluxion
sudo systemctl enable --now xstocks-api
# Optional second market (after M7-3 collector lands):
# sudo systemctl enable --now xstocks-collector@binance-pancake

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

## Redeploy (one command from a laptop)

```bash
# Build Next static export locally, rsync www + API sources, restart unit
./scripts/deploy-web.sh xstocks@whi715-vps

# Variants
./scripts/deploy-web.sh xstocks@host --skip-build   # reuse web/out
./scripts/deploy-web.sh xstocks@host --www-only     # static only
./scripts/deploy-web.sh xstocks@host --api-only     # Python only
```

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
