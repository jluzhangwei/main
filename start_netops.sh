#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
RUN_DIR="$ROOT_DIR/.run"
LOG_DIR="$ROOT_DIR/.logs"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

mkdir -p "$RUN_DIR" "$LOG_DIR"

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

select_backend_python() {
  if command_exists python3.11; then
    echo "python3.11"
    return 0
  fi
  if command_exists python3; then
    echo "python3"
    return 0
  fi
  return 1
}

port_pid() {
  local port="$1"
  lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
}

is_running_pid() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

start_detached() {
  local log_file="$1"
  shift
  if command_exists setsid; then
    nohup setsid "$@" >"$log_file" 2>&1 < /dev/null &
  else
    nohup "$@" >"$log_file" 2>&1 < /dev/null &
  fi
  echo $!
}

wait_port_up() {
  local port="$1"
  local retries="${2:-30}"
  local i
  for i in $(seq 1 "$retries"); do
    if [[ -n "$(port_pid "$port")" ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

ensure_backend_env() {
  local py_cmd=""
  py_cmd="$(select_backend_python || true)"
  if [[ -z "$py_cmd" ]]; then
    echo "[backend] python3.11/python3 not found. Please install Python 3.11 first."
    exit 1
  fi

  if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
    if ! "$BACKEND_DIR/.venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      echo "[backend] existing .venv uses unsupported Python (<3.11), recreating..."
      rm -rf "$BACKEND_DIR/.venv"
    fi
  fi

  if [[ ! -x "$BACKEND_DIR/.venv/bin/uvicorn" ]]; then
    echo "[backend] .venv not found, creating..."
    (cd "$BACKEND_DIR" && "$py_cmd" -m venv .venv)
    echo "[backend] installing dependencies..."
    (cd "$BACKEND_DIR" && ./.venv/bin/pip install -e .[dev])
  fi
}

ensure_frontend_env() {
  if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    echo "[frontend] node_modules not found, installing..."
    if ! command_exists npm; then
      echo "[frontend] npm not found. Please install Node.js + npm first."
      exit 1
    fi
    (cd "$FRONTEND_DIR" && npm install)
  fi
}

start_backend() {
  local existing
  existing="$(port_pid "$BACKEND_PORT")"
  if [[ -n "$existing" ]]; then
    echo "[backend] already listening on :$BACKEND_PORT (pid=$existing)"
    echo "$existing" >"$BACKEND_PID_FILE"
    return 0
  fi

  ensure_backend_env
  echo "[backend] starting on :$BACKEND_PORT ..."
  (
    cd "$BACKEND_DIR"
    start_detached "$BACKEND_LOG" ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" >"$BACKEND_PID_FILE"
  )

  if wait_port_up "$BACKEND_PORT" 30; then
    echo "[backend] started."
  else
    echo "[backend] failed to start. tail -n 80 $BACKEND_LOG"
    exit 1
  fi
}

start_frontend() {
  local existing
  existing="$(port_pid "$FRONTEND_PORT")"
  if [[ -n "$existing" ]]; then
    echo "[frontend] already listening on :$FRONTEND_PORT (pid=$existing)"
    echo "$existing" >"$FRONTEND_PID_FILE"
    return 0
  fi

  ensure_frontend_env
  echo "[frontend] starting on :$FRONTEND_PORT ..."
  (
    cd "$FRONTEND_DIR"
    start_detached "$FRONTEND_LOG" npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT" >"$FRONTEND_PID_FILE"
  )

  if wait_port_up "$FRONTEND_PORT" 30; then
    echo "[frontend] started."
  else
    echo "[frontend] failed to start. tail -n 80 $FRONTEND_LOG"
    exit 1
  fi
}

stop_one() {
  local name="$1"
  local pid_file="$2"
  local port="$3"
  local pid=""

  if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file" 2>/dev/null || true)"
  fi
  if [[ -z "$pid" ]]; then
    pid="$(port_pid "$port")"
  fi

  if [[ -z "$pid" ]]; then
    echo "[$name] not running."
    return 0
  fi

  echo "[$name] stopping pid=$pid ..."
  kill "$pid" 2>/dev/null || true
  sleep 1
  if is_running_pid "$pid"; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pid_file"
  echo "[$name] stopped."
}

status_one() {
  local name="$1"
  local port="$2"
  local pid
  pid="$(port_pid "$port")"
  if [[ -n "$pid" ]]; then
    echo "[$name] running pid=$pid port=$port"
  else
    echo "[$name] not running"
  fi
}

start_all() {
  start_backend
  start_frontend
  echo ""
  echo "NetOps started:"
  echo "- Backend:  http://127.0.0.1:${BACKEND_PORT}"
  echo "- Frontend: http://127.0.0.1:${FRONTEND_PORT}"
  echo "- Logs: $BACKEND_LOG | $FRONTEND_LOG"
}

stop_all() {
  stop_one "frontend" "$FRONTEND_PID_FILE" "$FRONTEND_PORT"
  stop_one "backend" "$BACKEND_PID_FILE" "$BACKEND_PORT"
}

status_all() {
  status_one "backend" "$BACKEND_PORT"
  status_one "frontend" "$FRONTEND_PORT"
}

ACTION="${1:-start}"
case "$ACTION" in
  start)
    start_all
    ;;
  stop)
    stop_all
    ;;
  restart)
    stop_all
    start_all
    ;;
  status)
    status_all
    ;;
  *)
    echo "Usage: $0 [start|stop|restart|status]"
    exit 1
    ;;
esac
