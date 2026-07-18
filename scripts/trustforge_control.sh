#!/bin/zsh
set -u

ROOT="${TRUSTFORGE_HOME:-/Users/apple/HurricaneSoft/trustforge}"
PID_FILE="${TRUSTFORGE_PID_FILE:-$ROOT/out/trustforge-web.pid}"
LOG_FILE="${TRUSTFORGE_LOG_FILE:-$ROOT/out/trustforge-web.log}"
PORT="${PORT:-8080}"
PYTHON_BIN="${TRUSTFORGE_PYTHON:-$ROOT/.venv/bin/python}"

cd "$ROOT" || exit 1
mkdir -p "$ROOT/out"

is_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

port_listening() {
  command -v lsof >/dev/null 2>&1 || return 1
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
}

port_pid() {
  command -v lsof >/dev/null 2>&1 || return 1
  lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1
}

adopt_port_listener() {
  local pid
  pid="$(port_pid)"
  [[ -n "$pid" ]] || return 1
  echo "$pid" >"$PID_FILE"
  return 0
}

case "${1:-status}" in
  start)
    PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m trustforge.cli control start --reason "local operator start"
    if is_running; then
      echo "trustforge web already running: $(cat "$PID_FILE")"
      exit 0
    fi
    if port_listening; then
      if adopt_port_listener; then
        echo "trustforge web already listening on port=$PORT; adopted pid=$(cat "$PID_FILE")"
      else
        echo "port=$PORT is already in use but no pid could be read" >&2
        exit 1
      fi
      exit 0
    fi
    TRUSTFORGE_ENV="${TRUSTFORGE_ENV:-local}" \
    TRUSTFORGE_DISABLE_ADMIN_CONFIG="${TRUSTFORGE_DISABLE_ADMIN_CONFIG:-1}" \
    PYTHONPATH="$ROOT/src" PORT="$PORT" "$PYTHON_BIN" -m trustforge.web >>"$LOG_FILE" 2>&1 &
    echo "$!" >"$PID_FILE"
    sleep 0.4
    if is_running; then
      echo "trustforge web started pid=$(cat "$PID_FILE") port=$PORT log=$LOG_FILE"
    elif port_listening; then
      echo "trustforge web listening on port=$PORT, but pid $(cat "$PID_FILE") exited; leaving stale pid file for inspection"
    else
      echo "trustforge web failed to start; see $LOG_FILE" >&2
      exit 1
    fi
    ;;
  stop)
    PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m trustforge.cli control stop --reason "local operator stop"
    if is_running; then
      pid="$(cat "$PID_FILE")"
      if kill "$pid"; then
        echo "trustforge web stopped pid=$pid"
      else
        echo "trustforge web stop failed pid=$pid" >&2
        exit 1
      fi
    elif port_listening && adopt_port_listener; then
      pid="$(cat "$PID_FILE")"
      if kill "$pid"; then
        echo "trustforge web stopped adopted pid=$pid"
      else
        echo "trustforge web stop failed adopted pid=$pid" >&2
        exit 1
      fi
    else
      echo "trustforge web not running"
    fi
    ;;
  status)
    PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m trustforge.cli control status
    if is_running; then
      echo "web=running pid=$(cat "$PID_FILE") port=$PORT"
    elif port_listening && adopt_port_listener; then
      echo "web=running pid=$(cat "$PID_FILE") port=$PORT adopted=true"
    elif port_listening; then
      echo "web=running port=$PORT pid=unknown_or_external"
    else
      echo "web=stopped"
    fi
    ;;
  *)
    echo "usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
