#!/usr/bin/env bash
# One-shot local dogfood for the full stack (WHI-757+): live collector +
# read-only API + Web panel.
#
# Starts:
#   - one monitor.collector per market in DEV_MARKETS (default: both markets)
#   - monitor.api --market $DEV_API_MARKET on http://127.0.0.1:8000
#   - Next.js dev  on http://localhost:3000  (NEXT_PUBLIC_API_BASE → API)
#
# Collectors make the panel *live*: each writes data/monitor-{market}.db.
# Skip them with --no-collector to browse a static snapshot (then `pull` first).
#
# WHI-825: every market is supervised equally — per-market pid/log
# (collector-{market}.pid/.log), auto-restart wrapper on non-zero exit, and
# status red-flags any down market with a one-line restart command.
#
# Usage:
#   ./scripts/dev-web.sh start                 # collectors + API + Web (live)
#   ./scripts/dev-web.sh start --no-collector  # API + Web over existing journal
#   ./scripts/dev-web.sh start --pull          # implies --no-collector: snapshot
#                                              # from VPS, then browse it
#   ./scripts/dev-web.sh stop
#   ./scripts/dev-web.sh restart [flags as start]
#   ./scripts/dev-web.sh status
#   ./scripts/dev-web.sh pull                  # refresh default API market journal
#                                              # (refuses while that collector runs)
#   ./scripts/dev-web.sh logs [collector|api|web|market-id]  # tail (default: all)
#   ./scripts/dev-web.sh clean                 # truncate .dev/*.log in place
#                                              # (safe while running; run it
#                                              # whenever `status` warns)
#
# Env (optional):
#   DEV_MARKETS=bybit-fluxion,binance-pancake
#                                 # comma-separated; default BOTH markets (WHI-825)
#   DEV_MARKET=bybit-fluxion      # legacy single-market alias; sets DEV_MARKETS
#   DEV_API_MARKET=bybit-fluxion  # which market the API binds (default: first)
#   DEV_API_HOST=127.0.0.1
#   DEV_API_PORT=8000
#   DEV_WEB_PORT=3000
#   DEV_API_RELOAD=1              # set 0 to disable uvicorn --reload
#   DEV_COLLECTOR=1               # set 0 for a permanent --no-collector
#   DEV_COLLECTOR_RESTART=1       # auto-restart collectors on non-zero exit
#   DEV_COLLECTOR_HB_STALE_S=90   # WHI-835: kill -9 if journal heartbeat older
#   DEV_COLLECTOR_HB_CHECK_S=10   # how often the wrapper polls heartbeat
#   DEV_COLLECTOR_HB_GRACE_S=90   # wait this long after spawn before enforcing
#   DEV_LOG_WARN_MB=200           # `status` warns above this per-log size
#   DEV_VPS_HOST=whi715-vps       # ssh Host for pull
#   DEV_REMOTE_DB=.../data/monitor-bybit-fluxion.db   # (legacy monitor.db ok)
#   DEV_SQLITE_PATH=data/monitor-bybit-fluxion.db     # override local journal
#   DEV_DIR=.dev                  # pid/log dir under repo root
#
# Prerequisites:
#   - uv + repo .venv (uv sync once)
#   - node/npm (web deps: npm ci once, auto if node_modules missing)
#   - collector: .env with MANTLE_RPC_URL (falls back to throttled public RPC)
#   - config/api.yaml cors_origins includes http://localhost:3000 (local
#     Next is cross-origin; empty list is correct on the VPS only)
#   - for pull: ssh BatchMode access to DEV_VPS_HOST
#
# Disk note: the collector log is the growth risk (verbose httpx lines, runs
# for hours). Every `start`/`restart` truncates all logs; `clean` truncates
# them in place mid-run (append-mode fds keep writing correctly after).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEV_DIR_REL="${DEV_DIR:-.dev}"
DEV_DIR_ABS="$ROOT/$DEV_DIR_REL"
PID_API="$DEV_DIR_ABS/api.pid"
PID_WEB="$DEV_DIR_ABS/web.pid"
LOG_API="$DEV_DIR_ABS/api.log"
LOG_WEB="$DEV_DIR_ABS/web.log"

# WHI-825: multi-market supervision. DEV_MARKETS wins; DEV_MARKET is a
# single-market alias. Default is both configured markets (no single-market
# privilege). Per-market pid/log: collector-{market}.pid / .log
# (legacy .dev/collector.pid and collector-binance.log are no longer used).
if [[ -n "${DEV_MARKETS:-}" ]]; then
  IFS=',' read -r -a MARKETS <<< "${DEV_MARKETS}"
elif [[ -n "${DEV_MARKET:-}" ]]; then
  MARKETS=("${DEV_MARKET}")
else
  MARKETS=(bybit-fluxion binance-pancake)
fi
# Trim whitespace around market ids
_MARKETS_TRIM=()
for _m in "${MARKETS[@]}"; do
  _m="$(echo "$_m" | tr -d '[:space:]')"
  [[ -n "$_m" ]] && _MARKETS_TRIM+=("$_m")
done
MARKETS=("${_MARKETS_TRIM[@]}")
if [[ ${#MARKETS[@]} -lt 1 ]]; then
  echo "dev-web: DEV_MARKETS/DEV_MARKET resolved to empty list" >&2
  exit 1
fi

API_MARKET="${DEV_API_MARKET:-${MARKETS[0]}}"
MARKET="$API_MARKET"  # backward-compat alias used by pull/sqlite defaults
API_HOST="${DEV_API_HOST:-127.0.0.1}"
API_PORT="${DEV_API_PORT:-8000}"
WEB_PORT="${DEV_WEB_PORT:-3000}"
API_RELOAD="${DEV_API_RELOAD:-1}"
COLLECTOR="${DEV_COLLECTOR:-1}"
COLLECTOR_RESTART="${DEV_COLLECTOR_RESTART:-1}"
# WHI-835: external heartbeat stall monitor (backup when in-process exit hangs).
# Must exceed watchdog check_interval + a little; 90s is well under exit_idle.
COLLECTOR_HB_STALE_S="${DEV_COLLECTOR_HB_STALE_S:-90}"
COLLECTOR_HB_CHECK_S="${DEV_COLLECTOR_HB_CHECK_S:-10}"
COLLECTOR_HB_GRACE_S="${DEV_COLLECTOR_HB_GRACE_S:-90}"
LOG_WARN_MB="${DEV_LOG_WARN_MB:-200}"
VPS_HOST="${DEV_VPS_HOST:-whi715-vps}"
REMOTE_DB="${DEV_REMOTE_DB:-/root/dev/bybit-mantle-arbitrage-monitor/data/monitor-${MARKET}.db}"
SQLITE_PATH="${DEV_SQLITE_PATH:-data/monitor-${MARKET}.db}"

pid_col() { echo "$DEV_DIR_ABS/collector-$1.pid"; }
log_col() { echo "$DEV_DIR_ABS/collector-$1.log"; }
sqlite_for() { echo "data/monitor-$1.db"; }

# Read collector_heartbeat_ms from a journal.
# Prints a single token line: OK <age_s> | STALE <age_s> | MISSING | ERROR
# Never kill on ERROR (probe failure ≠ stalled collector) — WHI-835 review.
_run_heartbeat_probe() {
  local py_bin="$1"
  local db="$2"
  local stale_s="$3"
  "$py_bin" - "$db" "$stale_s" <<'PY' 2>/dev/null
import sqlite3, sys, time
db, stale_s = sys.argv[1], int(sys.argv[2])
try:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = con.execute(
        "SELECT value FROM meta WHERE key = 'collector_heartbeat_ms'"
    ).fetchone()
    con.close()
except Exception:
    print("ERROR")
    raise SystemExit(0)
if not row or row[0] in (None, ""):
    print("MISSING")
    raise SystemExit(0)
try:
    hb = int(row[0])
except ValueError:
    print("ERROR")
    raise SystemExit(0)
age_s = max(0, int(time.time() * 1000 - hb) // 1000)
if age_s <= stale_s:
    print(f"OK {age_s}")
else:
    print(f"STALE {age_s}")
raise SystemExit(0)
PY
}

# Args: db_path stale_s
heartbeat_age_status() {
  local db="$1"
  local stale_s="$2"
  local out py
  # Prefer project venv interpreter — avoids mis-classifying `uv run`
  # lock/resync failures as a stalled heartbeat.
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    py="$ROOT/.venv/bin/python"
  else
    py="$(command -v python3 || true)"
  fi
  if [[ -z "$py" ]]; then
    echo "ERROR"
    return 0
  fi
  out="$(_run_heartbeat_probe "$py" "$db" "$stale_s")" || {
    echo "ERROR"
    return 0
  }
  # Only accept known tokens; anything else is ERROR (do not kill).
  case "${out%% *}" in
    OK|STALE|MISSING|ERROR) printf '%s\n' "$out" ;;
    *) echo "ERROR" ;;
  esac
}

API_BASE="http://${API_HOST}:${API_PORT}"
WEB_URL="http://localhost:${WEB_PORT}"

die() { echo "dev-web: $*" >&2; exit 1; }
info() { echo "dev-web: $*"; }

usage() {
  # Print the leading comment block (everything between the shebang and the
  # first non-comment line), stripped of the leading `# `.
  awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "$0"
  exit 2
}

ensure_dev_dir() {
  mkdir -p "$DEV_DIR_ABS"
}

pid_alive() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

read_pid() {
  local file="$1"
  if [[ -f "$file" ]]; then
    tr -d '[:space:]' <"$file"
  fi
}

# Kill a pid and its descendants (uvicorn --reload / next spawn children).
kill_tree() {
  local pid="$1"
  local sig="${2:-TERM}"
  local child
  # shellcheck disable=SC2046
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_tree "$child" "$sig"
  done
  kill -"$sig" "$pid" 2>/dev/null || true
}

stop_pidfile() {
  local name="$1"
  local file="$2"
  local pid
  pid="$(read_pid "$file" || true)"
  if pid_alive "$pid"; then
    info "stopping $name (pid $pid)"
    kill_tree "$pid" TERM
    # Wait up to ~5s, then SIGKILL the leftover tree.
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
      pid_alive "$pid" || break
      sleep 0.5
    done
    if pid_alive "$pid"; then
      info "force-killing $name (pid $pid)"
      kill_tree "$pid" KILL
    fi
  elif [[ -n "${pid:-}" ]]; then
    info "$name pid $pid not running (stale pidfile)"
  fi
  rm -f "$file"
}

# Last-resort: free a port if something matching our process still holds it.
free_port_if_ours() {
  local port="$1"
  local pattern="$2"
  local pids
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  [[ -z "$pids" ]] && return 0
  local p cmd
  for p in $pids; do
    cmd="$(ps -p "$p" -o args= 2>/dev/null || true)"
    if [[ "$cmd" == *"$pattern"* ]]; then
      info "freeing :$port (pid $p: $cmd)"
      kill_tree "$p" TERM
      sleep 0.3
      pid_alive "$p" && kill_tree "$p" KILL
    else
      die "port $port is in use by unrelated process (pid $p): $cmd"
    fi
  done
}

port_listening() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1
}

check_cors() {
  # Local Next hits the API cross-origin; empty cors_origins → browser blocks.
  if ! grep -Eq 'cors_origins:[[:space:]]*\[' config/api.yaml \
    && ! grep -Eq '^\s+-\s+"http://(localhost|127\.0\.0\.1):'"$WEB_PORT"'"' config/api.yaml; then
    # YAML list form under cors_origins:
    if ! awk '
      /^cors_origins:/ { inlist=1; next }
      inlist && /^[^[:space:]#]/ { exit }
      inlist && /localhost|127\.0\.0\.1/ { found=1 }
      END { exit found ? 0 : 1 }
    ' config/api.yaml; then
      info "warning: config/api.yaml cors_origins has no localhost origin"
      info "  browser dogfood needs e.g. - \"http://localhost:${WEB_PORT}\""
      info "  (empty list is correct on the VPS behind nginx same-origin)"
    fi
  fi
}

cmd_pull() {
  ensure_dev_dir
  # A pull clobbers the local journal; never do that under a live writer.
  local cpid
  cpid="$(read_pid "$(pid_col "$MARKET")" || true)"
  if pid_alive "$cpid"; then
    die "local collector for ${MARKET} is running (pid $cpid) and owns ${SQLITE_PATH}; stop first"
  fi
  info "snapshotting journal on ${VPS_HOST}:${REMOTE_DB}"
  # shellcheck disable=SC2029
  ssh -o BatchMode=yes -o ConnectTimeout=15 "$VPS_HOST" "python3 - <<'PY'
import sqlite3
from pathlib import Path
src_path = Path('''${REMOTE_DB}''')
dst_path = Path('/tmp/monitor-backup.db')
if not src_path.is_file():
    raise SystemExit(f'missing remote journal: {src_path}')
if dst_path.exists():
    dst_path.unlink()
# uri readonly can still back up a live WAL DB into a consistent snapshot
src = sqlite3.connect(f'file:{src_path}?mode=ro', uri=True, timeout=60)
dst = sqlite3.connect(dst_path)
with dst:
    src.backup(dst)
dst.close()
src.close()
v = sqlite3.connect(dst_path)
ok = v.execute('PRAGMA integrity_check').fetchone()[0]
n = v.execute('select count(*) from bybit_book').fetchone()[0]
v.close()
if ok != 'ok':
    raise SystemExit(f'integrity_check failed: {ok}')
print(f'backup ok bybit_book={n} size={dst_path.stat().st_size}')
PY"

  mkdir -p "$(dirname "$SQLITE_PATH")"
  info "rsync → ${SQLITE_PATH}"
  rsync -az "${VPS_HOST}:/tmp/monitor-backup.db" "$SQLITE_PATH"
  # Drop any leftover WAL from a prior hot-copy; backup is a single file.
  rm -f "${SQLITE_PATH}-wal" "${SQLITE_PATH}-shm"

  python3 - <<PY
import sqlite3
from pathlib import Path
p = Path("${SQLITE_PATH}")
c = sqlite3.connect(p)
ok = c.execute("PRAGMA integrity_check").fetchone()[0]
n = c.execute("select count(*) from bybit_book").fetchone()[0]
c.close()
if ok != "ok":
    raise SystemExit(f"local integrity_check failed: {ok}")
print(f"dev-web: local journal ok bybit_book={n} path={p.resolve()}")
PY
}

ensure_journal() {
  if [[ ! -f "$SQLITE_PATH" ]]; then
    die "no journal at ${SQLITE_PATH}. Run: $0 pull   (or start --pull, or drop --no-collector)"
  fi
}

# Collector creates the journal (schema init) on first run; the API only
# attaches a reader when the file exists, so wait for it before starting API.
wait_journal() {
  local tries="${1:-60}"
  local _i
  for _i in $(seq 1 "$tries"); do
    [[ -f "$SQLITE_PATH" ]] && return 0
    sleep 0.5
  done
  die "collector did not create ${SQLITE_PATH} within $((tries / 2))s. See ${DEV_DIR_REL}/collector-*.log"
}

ensure_web_deps() {
  if [[ ! -d web/node_modules ]]; then
    info "web/node_modules missing → npm ci"
    (cd web && npm ci)
  fi
}

wait_http() {
  local url="$1"
  local label="$2"
  local tries="${3:-40}"
  local i
  for i in $(seq 1 "$tries"); do
    if curl -fsS -o /dev/null --connect-timeout 1 --max-time 2 "$url" 2>/dev/null; then
      return 0
    fi
    sleep 0.25
  done
  die "${label} did not become ready (${url}). See logs under ${DEV_DIR_REL}/"
}


# Start one market collector under a restart wrapper (WHI-825 / WHI-835).
# Background job + disown so it survives the controlling shell (macOS has no
# setsid). Non-zero exit (watchdog) restarts when DEV_COLLECTOR_RESTART=1.
#
# WHI-835: the child is run in the background so we can also poll
# collector_heartbeat_ms. If the in-process watchdog stops writing but the
# process refuses to exit (hung to_thread / closed-client retries), the
# wrapper kill -9s it so restart can proceed. Dual layer with in-process
# hard os._exit after shutdown_grace_s.
start_one_collector() {
  local market="$1"
  local sqlite log pidfile
  sqlite="$(sqlite_for "$market")"
  log="$(log_col "$market")"
  pidfile="$(pid_col "$market")"
  info "starting collector market=${market} (journal ${sqlite})"
  : >"$log"
  (
    # set -e is inherited from the parent; capture exit without aborting the loop.
    set +e
    cd "$ROOT"
    while true; do
      uv run python -u -m monitor.collector --market "$market" --sqlite "$sqlite" \
        >>"$log" 2>&1 &
      child=$!
      started_s="$(date +%s)"
      # Supervise until the child exits: poll heartbeat as a stall detector.
      while kill -0 "$child" 2>/dev/null; do
        sleep "${COLLECTOR_HB_CHECK_S}"
        now_s="$(date +%s)"
        # Startup grace: heartbeat loop arms after cold start; don't kill early.
        if (( now_s - started_s < COLLECTOR_HB_GRACE_S )); then
          continue
        fi
        # Sentinel protocol (WHI-835 review): only STALE/MISSING trigger kill;
        # OK/ERROR/unknown → leave the child alone.
        hb_line="$(heartbeat_age_status "$ROOT/$sqlite" "$COLLECTOR_HB_STALE_S" || true)"
        hb_tok="${hb_line%% *}"
        hb_age="${hb_line#* }"
        if [[ "$hb_tok" == "STALE" ]]; then
          echo "dev-web: collector[${market}] heartbeat stale age=${hb_age}s (threshold=${COLLECTOR_HB_STALE_S}s); kill -9 pid=${child}" >>"$log"
          kill -9 "$child" 2>/dev/null || true
          break
        fi
        if [[ "$hb_tok" == "MISSING" ]]; then
          # No heartbeat after grace: process is up but not journaling.
          echo "dev-web: collector[${market}] no heartbeat after grace ${COLLECTOR_HB_GRACE_S}s; kill -9 pid=${child}" >>"$log"
          kill -9 "$child" 2>/dev/null || true
          break
        fi
      done
      wait "$child" 2>/dev/null
      code=$?
      if [[ "${COLLECTOR_RESTART}" != "1" ]]; then
        exit "$code"
      fi
      # Clean stop (SIGTERM → exit 0) ends the wrapper; non-zero → restart.
      # kill -9 yields 137 / 137+signal — also restart.
      if [[ "$code" -eq 0 ]]; then
        exit 0
      fi
      echo "dev-web: collector[${market}] exited code=${code}; restarting in 3s" >>"$log"
      sleep 3
    done
  ) &
  echo $! >"$pidfile"
  disown $! 2>/dev/null || true
}

start_all_collectors() {
  local m cpid log
  for m in "${MARKETS[@]}"; do
    start_one_collector "$m"
  done
  # Wait for the API market journal (API attaches a reader on that path).
  SQLITE_PATH="$(sqlite_for "$API_MARKET")"
  wait_journal
  for m in "${MARKETS[@]}"; do
    cpid="$(read_pid "$(pid_col "$m")")"
    log="$(log_col "$m")"
    pid_alive "$cpid" || die "collector[${m}] exited at startup. See ${log}"
  done
}

cmd_start() {
  local do_pull=0
  local collector="$COLLECTOR"
  local arg
  for arg in "$@"; do
    case "$arg" in
      --pull) do_pull=1 ;;
      --no-collector) collector=0 ;;
      -h|--help) usage ;;
      *) die "unknown start flag: $arg (try --pull | --no-collector)" ;;
    esac
  done
  # A pulled snapshot and a live local writer are mutually exclusive.
  if [[ "$do_pull" -eq 1 ]]; then
    collector=0
  fi

  ensure_dev_dir
  check_cors

  if [[ "$do_pull" -eq 1 ]]; then
    cmd_pull
  fi
  if [[ "$collector" -eq 0 ]]; then
    ensure_journal
  fi
  ensure_web_deps

  # Already running (everything we were asked for)?
  local apid wpid cpid m all_cols=1
  apid="$(read_pid "$PID_API" || true)"
  wpid="$(read_pid "$PID_WEB" || true)"
  if [[ "$collector" -eq 1 ]]; then
    for m in "${MARKETS[@]}"; do
      cpid="$(read_pid "$(pid_col "$m")" || true)"
      pid_alive "$cpid" || all_cols=0
    done
  fi
  if pid_alive "$apid" && pid_alive "$wpid" && port_listening "$API_PORT" && port_listening "$WEB_PORT" \
    && { [[ "$collector" -eq 0 ]] || [[ "$all_cols" -eq 1 ]]; }; then
    info "already running"
    info "  Web  ${WEB_URL}"
    info "  API  ${API_BASE}  (docs ${API_BASE}/docs)  market=${API_MARKET}"
    if [[ "$collector" -eq 1 ]]; then
      for m in "${MARKETS[@]}"; do
        cpid="$(read_pid "$(pid_col "$m")" || true)"
        info "  collector[${m}]  pid $cpid"
      done
    fi
    exit 0
  fi

  # Clean partial state (every market collector + api + web)
  for m in "${MARKETS[@]}"; do
    stop_pidfile "collector[$m]" "$(pid_col "$m")"
  done
  # Legacy single-market pid from pre-WHI-825 runs
  stop_pidfile "collector[legacy]" "$DEV_DIR_ABS/collector.pid"
  stop_pidfile "api" "$PID_API"
  stop_pidfile "web" "$PID_WEB"
  free_port_if_ours "$API_PORT" "monitor.api"
  free_port_if_ours "$WEB_PORT" "next"

  local reload_flag=()
  if [[ "$API_RELOAD" == "1" ]]; then
    reload_flag=(--reload)
  fi

  if [[ "$collector" -eq 1 ]]; then
    start_all_collectors
  else
    info "collector disabled (--no-collector); panel reads a static journal"
  fi

  info "starting API on ${API_BASE} (market=${API_MARKET})"
  : >"$LOG_API"
  (
    cd "$ROOT"
    # shellcheck disable=SC2086
    exec uv run python -m monitor.api --market "$API_MARKET" --host "$API_HOST" --port "$API_PORT" "${reload_flag[@]}"
  ) >>"$LOG_API" 2>&1 &
  echo $! >"$PID_API"

  info "starting Web on ${WEB_URL}"
  : >"$LOG_WEB"
  (
    cd "$ROOT/web"
    export NEXT_PUBLIC_API_BASE="$API_BASE"
    # next dev defaults to 3000; pin port for status/stop.
    exec npm run dev -- --port "$WEB_PORT"
  ) >>"$LOG_WEB" 2>&1 &
  echo $! >"$PID_WEB"

  wait_http "${API_BASE}/" "API"
  # /api/health may be 200 with ok:false (stale snapshot) — still proves routing.
  wait_http "${API_BASE}/api/health" "API /api/health"
  wait_http "${WEB_URL}/" "Web"

  info "ready"
  info "  Web  ${WEB_URL}"
  info "  API  ${API_BASE}  (docs ${API_BASE}/docs)  market=${API_MARKET}"
  if [[ "$collector" -eq 1 ]]; then
    for m in "${MARKETS[@]}"; do
      info "  journal[${m}]  $(sqlite_for "$m") (live)"
    done
  else
    info "  journal  ${SQLITE_PATH} (static snapshot; panel will show stale)"
  fi
  info "  logs     $0 logs   (files under ${DEV_DIR_REL}/)"
  info "  clean    $0 clean  (truncate logs; collector log grows for hours)"
  info "  stop     $0 stop"
}

soft_free_port() {
  # Like free_port_if_ours but never aborts on unrelated listeners.
  local port="$1"
  local pattern="$2"
  local pids p cmd
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  for p in $pids; do
    cmd="$(ps -p "$p" -o args= 2>/dev/null || true)"
    if [[ "$cmd" == *"$pattern"* ]]; then
      info "freeing :$port (pid $p)"
      kill_tree "$p" TERM
      sleep 0.2
      pid_alive "$p" && kill_tree "$p" KILL
    fi
  done
}

cmd_stop() {
  ensure_dev_dir
  # Collectors first: SIGTERM lets each daemon flush + close SQLite cleanly.
  local m
  for m in "${MARKETS[@]}"; do
    stop_pidfile "collector[$m]" "$(pid_col "$m")"
  done
  stop_pidfile "collector[legacy]" "$DEV_DIR_ABS/collector.pid"
  stop_pidfile "api" "$PID_API"
  stop_pidfile "web" "$PID_WEB"
  soft_free_port "$API_PORT" "monitor.api"
  soft_free_port "$WEB_PORT" "next"
  info "stopped"
}

log_size_mb() {
  local file="$1"
  [[ -f "$file" ]] || { echo 0; return; }
  echo $(( $(wc -c <"$file") / 1048576 ))
}

warn_big_logs() {
  local f mb m
  local files=("$LOG_API" "$LOG_WEB")
  for m in "${MARKETS[@]}"; do
    files+=("$(log_col "$m")")
  done
  for f in "${files[@]}"; do
    [[ -f "$f" ]] || continue
    mb="$(log_size_mb "$f")"
    if (( mb >= LOG_WARN_MB )); then
      info "⚠ $(basename "$f") is ${mb}MB (warn at ${LOG_WARN_MB}MB) — run: $0 clean"
    fi
  done
}

cmd_status() {
  ensure_dev_dir
  local apid wpid cpid m any_down=0 db
  local RED=$'\033[31m' GRN=$'\033[32m' RST=$'\033[0m'
  apid="$(read_pid "$PID_API" || true)"
  wpid="$(read_pid "$PID_WEB" || true)"

  info "markets  ${MARKETS[*]}  (api_market=${API_MARKET})"
  for m in "${MARKETS[@]}"; do
    cpid="$(read_pid "$(pid_col "$m")" || true)"
    db="$(sqlite_for "$m")"
    if pid_alive "$cpid"; then
      info "collector[${m}]  ${GRN}RUNNING${RST}  pid=$cpid  → ${db}"
    else
      any_down=1
      info "collector[${m}]  ${RED}STOPPED${RST}  (pidfile=${cpid:-none})"
      info "  → restart: $0 start   # or: nohup uv run python -u -m monitor.collector --market ${m} >> $(log_col "$m") 2>&1 &"
    fi
    if [[ -f "$db" ]]; then
      info "  journal[${m}]  $(ls -lh "$db" | awk '{print $5, $9}')"
    else
      info "  journal[${m}]  missing (${db})"
    fi
  done
  if [[ "$any_down" -eq 1 ]]; then
    info "${RED}⚠ one or more collectors are down — panel may show feed down / frozen numbers${RST}"
  fi

  if pid_alive "$apid"; then
    info "api  running  pid=$apid  ${API_BASE}"
  else
    info "api  stopped  (pidfile=${apid:-none})"
  fi
  if pid_alive "$wpid"; then
    info "web  running  pid=$wpid  ${WEB_URL}"
  else
    info "web  stopped  (pidfile=${wpid:-none})"
  fi

  if port_listening "$API_PORT"; then
    if curl -fsS -o /dev/null --max-time 2 "${API_BASE}/api/health" 2>/dev/null; then
      local health
      health="$(curl -fsS --max-time 2 "${API_BASE}/api/health" 2>/dev/null || true)"
      info "health  $health"
    else
      info "port :${API_PORT} listening but /api/health failed"
    fi
  fi
  warn_big_logs
}

cmd_clean() {
  ensure_dev_dir
  # Truncate in place: every writer opened its log in append mode (>>), so
  # the fd offset resets safely and writing continues — no restart needed.
  local f freed_kb=0 before
  local any=0
  for f in "$DEV_DIR_ABS"/*.log; do
    [[ -f "$f" ]] || continue
    any=1
    before=$(( $(wc -c <"$f") / 1024 ))
    : >"$f"
    freed_kb=$(( freed_kb + before ))
    info "truncated $(basename "$f") (freed ${before}KB)"
  done
  # WHI-825: drop pre-market-aware naming fork (manual binance start).
  if [[ -f "$DEV_DIR_ABS/collector-binance.log" ]]; then
    rm -f "$DEV_DIR_ABS/collector-binance.log"
    info "removed legacy collector-binance.log (use collector-binance-pancake.log)"
  fi
  if [[ -f "$DEV_DIR_ABS/collector.pid" ]]; then
    rm -f "$DEV_DIR_ABS/collector.pid"
    info "removed legacy collector.pid (use collector-{market}.pid)"
  fi
  if [[ "$any" -eq 0 ]]; then
    info "no logs under ${DEV_DIR_REL}/ — nothing to clean"
  else
    info "total freed ~$(( freed_kb / 1024 ))MB"
  fi
}

cmd_logs() {
  ensure_dev_dir
  local which="${1:-all}" m
  case "$which" in
    collector)
      local files=()
      for m in "${MARKETS[@]}"; do
        [[ -f "$(log_col "$m")" ]] && files+=("$(log_col "$m")")
      done
      [[ ${#files[@]} -gt 0 ]] || die "no collector logs yet"
      exec tail -n 80 -f "${files[@]}"
      ;;
    api)
      [[ -f "$LOG_API" ]] || die "no api log yet"
      exec tail -n 80 -f "$LOG_API"
      ;;
    web)
      [[ -f "$LOG_WEB" ]] || die "no web log yet"
      exec tail -n 80 -f "$LOG_WEB"
      ;;
    all|both|"")
      local files=()
      for m in "${MARKETS[@]}"; do
        [[ -f "$(log_col "$m")" ]] && files+=("$(log_col "$m")")
      done
      [[ -f "$LOG_API" ]] && files+=("$LOG_API")
      [[ -f "$LOG_WEB" ]] && files+=("$LOG_WEB")
      [[ ${#files[@]} -gt 0 ]] || die "no logs yet (start first)"
      exec tail -n 40 -f "${files[@]}"
      ;;
    *)
      if [[ -f "$(log_col "$which")" ]]; then
        exec tail -n 80 -f "$(log_col "$which")"
      fi
      die "logs target must be collector|api|web|<market-id> (got: $which)"
      ;;
  esac
}

cmd_restart() {
  cmd_stop
  cmd_start "$@"
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    start)   cmd_start "$@" ;;
    stop)    cmd_stop ;;
    restart) cmd_restart "$@" ;;
    status)  cmd_status ;;
    pull)    cmd_pull ;;
    logs)    cmd_logs "$@" ;;
    clean)   cmd_clean ;;
    -h|--help|help|"") usage ;;
    *) die "unknown command: $cmd (try start|stop|restart|status|pull|logs|clean)" ;;
  esac
}

main "$@"
