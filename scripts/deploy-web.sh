#!/usr/bin/env bash
# Deploy web static export + (optionally) restart the API on the VPS (WHI-757).
#
# Build happens on the *caller* machine (laptop / CI). The 1GB VPS must never
# run `next build`.
#
# Usage:
#   ./scripts/deploy-web.sh user@host
#   DEPLOY_HOST=user@whi715-vps ./scripts/deploy-web.sh
#   ./scripts/deploy-web.sh user@host --skip-build
#   ./scripts/deploy-web.sh user@host --api-only
#
# Layout expected on the remote (override with env):
#   REMOTE_WWW=/opt/xstocks/www          # nginx root
#   REMOTE_APP=/opt/xstocks/app          # python package checkout
#   SYSTEMD_UNIT=xstocks-api             # restarted after code sync
#
# Env:
#   DEPLOY_HOST   user@host (required if no positional arg)
#   SKIP_BUILD=1  skip `npm run build` in web/
#   SKIP_API=1    do not rsync Python sources / restart systemd
#   SKIP_WWW=1    do not rsync web/out
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${1:-${DEPLOY_HOST:-}}"
if [[ -z "${HOST}" || "${HOST}" == --* ]]; then
  echo "usage: $0 user@host [--skip-build|--api-only|--www-only]" >&2
  exit 2
fi
shift || true

SKIP_BUILD="${SKIP_BUILD:-0}"
SKIP_API="${SKIP_API:-0}"
SKIP_WWW="${SKIP_WWW:-0}"
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    --api-only) SKIP_WWW=1 ;;
    --www-only) SKIP_API=1 ;;
    *)
      echo "unknown flag: $arg" >&2
      exit 2
      ;;
  esac
done

REMOTE_WWW="${REMOTE_WWW:-/opt/xstocks/www}"
REMOTE_APP="${REMOTE_APP:-/opt/xstocks/app}"
SYSTEMD_UNIT="${SYSTEMD_UNIT:-xstocks-api}"

RSYNC_RSH="${RSYNC_RSH:-ssh}"
RSYNC=(rsync -az --delete -e "${RSYNC_RSH}")

if [[ "${SKIP_WWW}" != "1" ]]; then
  if [[ "${SKIP_BUILD}" != "1" ]]; then
    echo "==> building static export in web/"
    (
      cd "${ROOT}/web"
      if [[ ! -d node_modules ]]; then
        npm ci 2>/dev/null || npm install
      fi
      npm run build
    )
  fi
  if [[ ! -d "${ROOT}/web/out" ]]; then
    echo "missing web/out — run npm run build in web/ first" >&2
    exit 1
  fi
  echo "==> rsync web/out/ → ${HOST}:${REMOTE_WWW}/"
  "${RSYNC[@]}" "${ROOT}/web/out/" "${HOST}:${REMOTE_WWW}/"
fi

if [[ "${SKIP_API}" != "1" ]]; then
  echo "==> rsync API sources → ${HOST}:${REMOTE_APP}/"
  # Ship only what the API needs to import; leave local data/ and .venv alone.
  "${RSYNC[@]}" \
    --exclude '.venv/' \
    --exclude 'web/node_modules/' \
    --exclude 'web/.next/' \
    --exclude 'web/out/' \
    --exclude 'data/' \
    --exclude '.git/' \
    --exclude '**/__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude '.mypy_cache/' \
    --exclude '.ruff_cache/' \
    "${ROOT}/src/" "${HOST}:${REMOTE_APP}/src/"
  "${RSYNC[@]}" \
    "${ROOT}/config/" "${HOST}:${REMOTE_APP}/config/"
  "${RSYNC[@]}" \
    "${ROOT}/pyproject.toml" "${HOST}:${REMOTE_APP}/pyproject.toml"
  if [[ -f "${ROOT}/uv.lock" ]]; then
    "${RSYNC[@]}" "${ROOT}/uv.lock" "${HOST}:${REMOTE_APP}/uv.lock"
  fi

  echo "==> remote: uv sync + restart ${SYSTEMD_UNIT}"
  # shellcheck disable=SC2029
  ssh "${HOST}" "set -euo pipefail
    cd '${REMOTE_APP}'
    if command -v uv >/dev/null 2>&1; then
      uv sync --no-dev
    else
      echo 'uv not found on remote; ensure .venv already has fastapi/uvicorn' >&2
    fi
    if systemctl is-enabled '${SYSTEMD_UNIT}' >/dev/null 2>&1 \
       || systemctl cat '${SYSTEMD_UNIT}' >/dev/null 2>&1; then
      sudo systemctl restart '${SYSTEMD_UNIT}'
      sudo systemctl --no-pager --full status '${SYSTEMD_UNIT}' | head -20
    else
      echo 'systemd unit ${SYSTEMD_UNIT} not installed; skip restart' >&2
    fi
  "
fi

echo "==> deploy done"
echo "    www:  ${HOST}:${REMOTE_WWW}"
echo "    api:  ${HOST}:${REMOTE_APP}  (unit ${SYSTEMD_UNIT})"
echo "    tip:  curl -sS http://${HOST#*@}/api/health | head"
