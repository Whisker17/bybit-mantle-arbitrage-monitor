#!/usr/bin/env bash
# Deploy web static export + (optionally) restart the API on the VPS (WHI-757).
#
# Build happens on the *caller* machine (laptop / CI). The 1GB VPS must never
# run `next build`.
#
# Usage:
#   ./scripts/deploy-web.sh user@host
#   DEPLOY_HOST=user@whi715-vps ./scripts/deploy-web.sh
#   DEPLOY_HOST=user@host ./scripts/deploy-web.sh --api-only
#   ./scripts/deploy-web.sh user@host --skip-build
#   ./scripts/deploy-web.sh user@host --www-only
#
# Layout expected on the remote (override with env):
#   REMOTE_WWW=/opt/xstocks/www          # nginx root
#   REMOTE_APP=/opt/xstocks/app          # python package checkout
#   SYSTEMD_UNIT=xstocks-api             # restarted after code sync
#
# Env:
#   DEPLOY_HOST   user@host (required if no positional host arg)
#   SKIP_BUILD=1  skip `npm run build` in web/
#   SKIP_API=1    do not rsync Python sources / restart systemd
#   SKIP_WWW=1    do not rsync web/out
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

HOST="${DEPLOY_HOST:-}"
SKIP_BUILD="${SKIP_BUILD:-0}"
SKIP_API="${SKIP_API:-0}"
SKIP_WWW="${SKIP_WWW:-0}"

for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    --api-only) SKIP_WWW=1 ;;
    --www-only) SKIP_API=1 ;;
    -*)
      echo "unknown flag: $arg" >&2
      exit 2
      ;;
    *)
      if [[ -n "${HOST}" && "${HOST}" != "${arg}" ]]; then
        echo "host already set (${HOST}); unexpected arg: ${arg}" >&2
        exit 2
      fi
      HOST="${arg}"
      ;;
  esac
done

if [[ -z "${HOST}" ]]; then
  echo "usage: $0 [user@host] [--skip-build|--api-only|--www-only]" >&2
  echo "       DEPLOY_HOST=user@host $0 [--api-only]" >&2
  exit 2
fi

REMOTE_WWW="${REMOTE_WWW:-/opt/xstocks/www}"
REMOTE_APP="${REMOTE_APP:-/opt/xstocks/app}"
SYSTEMD_UNIT="${SYSTEMD_UNIT:-xstocks-api}"

RSYNC_RSH="${RSYNC_RSH:-ssh}"
# Static www uses --delete so removed assets disappear. Source/config do not.
RSYNC_WWW=(rsync -az --delete -e "${RSYNC_RSH}")
RSYNC_SRC=(rsync -az -e "${RSYNC_RSH}")

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
  "${RSYNC_WWW[@]}" "${ROOT}/web/out/" "${HOST}:${REMOTE_WWW}/"
fi

if [[ "${SKIP_API}" != "1" ]]; then
  echo "==> rsync API sources → ${HOST}:${REMOTE_APP}/"
  # Ship only what the API needs to import; leave local data/ and .venv alone.
  # No --delete: operator-local files under src/ must not be wiped.
  "${RSYNC_SRC[@]}" \
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
  # config: never --delete (keeps remote-only files). Checked-in YAML is still
  # overwritten; put operator path overrides in untracked *.local.yaml later.
  "${RSYNC_SRC[@]}" \
    "${ROOT}/config/" "${HOST}:${REMOTE_APP}/config/"
  "${RSYNC_SRC[@]}" \
    "${ROOT}/pyproject.toml" "${HOST}:${REMOTE_APP}/pyproject.toml"
  if [[ -f "${ROOT}/uv.lock" ]]; then
    "${RSYNC_SRC[@]}" "${ROOT}/uv.lock" "${HOST}:${REMOTE_APP}/uv.lock"
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
