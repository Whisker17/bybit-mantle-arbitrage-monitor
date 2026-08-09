# ADR-0003: Same-origin static panel from FastAPI (no public listener)

* Status: Accepted
* Date: 2026-08-09
* Issue: WHI-979
* Owner decision: serve `web/out` from the FastAPI process itself

## Context

WHI-757 shipped a VPS layout of **nginx** serving `web/out` + reverse-proxying
`/api/*` to uvicorn on `127.0.0.1:8000`. That is fine on a panel-only box
(whi715-vps style).

**arb-bot-vps** also runs live `arb-bot.service` and holds trading keys. Owner
decision 2026-08-09: **no public listener and no nginx** on that host. The
panel is reached only via SSH local forward:

```bash
ssh -N -L 8010:127.0.0.1:8000 \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 arb-bot-vps
```

Two edges bit within the hour of that decision:

1. Nothing served `web/out` after nginx was skipped — the export had no home.
2. `NEXT_PUBLIC_API_BASE` is build-time only; the bundle was welded to a local
   `-L` port. A dead tunnel produced the opaque browser string
   `Failed to fetch` with no base URL in the banner.

## Decision

1. **FastAPI mounts the static export** when `config/api.yaml` `static_dir` is
   set **and** the directory exists (`StaticFiles(..., html=True)` after the
   `/api` routers so it cannot swallow them). Default checked-in path is
   `/opt/xstocks/www` (rsync target of `scripts/deploy-web.sh`). Local/dev
   checkouts without that path stay API-only.
2. **Same-origin `apiBase ""`** for the VPS build — do not bake
   `NEXT_PUBLIC_API_BASE`. The env override remains for split-origin local
   dogfood.
3. **Bind stays `127.0.0.1`**. Tunnel access *is* the auth. No new public
   surface.
4. **Service discovery** is `GET /__meta` (stable with or without static).
   When static is off, `GET /` still returns the discovery JSON for API-only
   DX.
5. **Network failures** in `web/lib/api.ts` name the resolved base (or
   `same-origin`) so a dead tunnel / wrong `-L` port is self-evident.

## Consequences

* One tunnel serves both the panel HTML and `/api/*` (`/docs`,
  `/openapi.json` remain).
* Optional nginx reverse-proxy is still supported on panel-only hosts; leaving
  `static_dir` pointed at the export is harmless when nginx fronts traffic
  (browser never hits uvicorn for static). Prefer `static_dir: null` only if
  you intentionally want API-only on `:8000`.
* DESIGN §4.1 tech stack lists both shapes; this ADR is the arb-bot-vps
  constraint of record.

## Rejected

* Re-opening a public bind / auth on the panel.
* Re-introducing nginx on arb-bot-vps solely to host `web/out`.
* SPA catch-all fallback for missing export paths (404 must stay 404).
