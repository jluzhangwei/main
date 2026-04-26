#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$BASE_DIR/lldp_sql_service.py"
PID_FILE="$BASE_DIR/.lldp_service.pid"
LOG_FILE="$BASE_DIR/.lldp_service.log"
PORT="${LLDP_PORT:-18080}"
HOST="${LLDP_HOST:-127.0.0.1}"

if [[ -f "$BASE_DIR/.env.mysql" ]]; then
  DB_ENV_FILE_DEFAULT="$BASE_DIR/.env.mysql"
elif [[ -f "$BASE_DIR/../.env.mysql" ]]; then
  DB_ENV_FILE_DEFAULT="$BASE_DIR/../.env.mysql"
else
  DB_ENV_FILE_DEFAULT="$BASE_DIR/.env.mysql"
fi
DB_ENV_FILE_PATH="${DB_ENV_FILE:-$DB_ENV_FILE_DEFAULT}"

VENV_PY="$BASE_DIR/../.venv/bin/python"
if [[ -x "$VENV_PY" ]]; then
  PYTHON_BIN="$VENV_PY"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

PIP_BIN="${PYTHON_BIN} -m pip"
ACTION="${1:-start}"

print_header() {
  echo "=== LLDP SQL Service ==="
  echo "Base Dir    : $BASE_DIR"
  echo "Service File: $SERVICE_FILE"
  echo "Python      : $PYTHON_BIN"
  echo "Host/Port   : $HOST:$PORT"
  echo "DB_ENV_FILE : $DB_ENV_FILE_PATH"
  echo
}

port_query() {
  echo "[Port Check]"
  if lsof -iTCP:"$PORT" -sTCP:LISTEN -n -P; then
    true
  else
    echo "No process listening on $HOST:$PORT"
  fi
  echo
}

read_pid_file() {
  if [[ -f "$PID_FILE" ]]; then
    cat "$PID_FILE"
  fi
}

is_pid_running() {
  local pid="$1"
  [[ -n "$pid" ]] && ps -p "$pid" >/dev/null 2>&1
}

service_pid_by_port() {
  lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
}

status() {
  print_header
  local pid="$(read_pid_file || true)"
  if is_pid_running "$pid"; then
    echo "[Status] Running (pid from pid file: $pid)"
  else
    if [[ -n "$pid" ]]; then
      echo "[Status] PID file exists but process not running (stale pid: $pid)"
    else
      echo "[Status] No PID file"
    fi
  fi

  local port_pid
  port_pid="$(service_pid_by_port)"
  if [[ -n "$port_pid" ]]; then
    echo "[Status] Port $PORT is listening by pid: $port_pid"
  else
    echo "[Status] Port $PORT is not listening"
  fi

  port_query
  echo "[Logs] $LOG_FILE"
}

check_env() {
  echo "[Env Check]"
  if [[ ! -f "$SERVICE_FILE" ]]; then
    echo "ERROR: Service file not found: $SERVICE_FILE"
    exit 1
  fi

  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "ERROR: Python not found: $PYTHON_BIN"
    exit 1
  fi

  if [[ ! -f "$DB_ENV_FILE_PATH" ]]; then
    echo "WARN : DB env file not found: $DB_ENV_FILE_PATH"
    echo "       You can create it from: $BASE_DIR/.env.mysql.example"
  else
    echo "OK   : DB env file found"
    for key in CLI_DEVICE_USERNAME CLI_DEVICE_PASSWORD SMC_COMMAND; do
      if grep -Eq "^[[:space:]]*${key}=" "$DB_ENV_FILE_PATH"; then
        echo "OK   : $key configured (for CLI import)"
      else
        echo "WARN : $key not found in env file (CLI import may fail)"
      fi
    done
  fi

  local missing=0
  for mod in fastapi uvicorn pymysql; do
    if "$PYTHON_BIN" -c "import $mod" >/dev/null 2>&1; then
      echo "OK   : Python module '$mod'"
    else
      echo "MISSING: Python module '$mod'"
      missing=1
    fi
  done

  if [[ "$missing" -eq 1 ]]; then
    echo "Installing missing modules: fastapi uvicorn pymysql"
    $PIP_BIN install fastapi uvicorn pymysql
  fi
  echo
}

start() {
  print_header
  check_env

  local running_pid
  running_pid="$(service_pid_by_port)"
  if [[ -n "$running_pid" ]]; then
    echo "Service already listening on :$PORT (pid=$running_pid)."
    port_query
    return 0
  fi

  echo "Starting service..."
  (
    cd "$BASE_DIR"
    if command -v setsid >/dev/null 2>&1; then
      nohup env DB_ENV_FILE="$DB_ENV_FILE_PATH" \
        setsid "$PYTHON_BIN" -m uvicorn lldp_sql_service:app --host "$HOST" --port "$PORT" \
        >"$LOG_FILE" 2>&1 < /dev/null &
    else
      nohup env DB_ENV_FILE="$DB_ENV_FILE_PATH" \
        "$PYTHON_BIN" -m uvicorn lldp_sql_service:app --host "$HOST" --port "$PORT" \
        >"$LOG_FILE" 2>&1 < /dev/null &
    fi
    echo $! >"$PID_FILE"
  )

  sleep 1
  local pid
  pid="$(read_pid_file || true)"
  if is_pid_running "$pid"; then
    echo "Start issued (pid=$pid)."
  else
    echo "ERROR: Start failed. Check logs: $LOG_FILE"
    tail -n 80 "$LOG_FILE" || true
    exit 1
  fi

  sleep 1
  local port_pid
  port_pid="$(service_pid_by_port)"
  if [[ -n "$port_pid" ]]; then
    echo "Service ready at http://$HOST:$PORT/lldp.html"
  else
    echo "WARN: process started but port is not listening yet."
  fi

  echo
  port_query
}

stop() {
  print_header
  local pid="$(read_pid_file || true)"
  local port_pid="$(service_pid_by_port)"

  if [[ -n "$pid" ]] && is_pid_running "$pid"; then
    echo "Stopping pid from pid file: $pid"
    kill "$pid" || true
  fi

  if [[ -n "$port_pid" ]] && is_pid_running "$port_pid"; then
    echo "Stopping pid on port $PORT: $port_pid"
    kill "$port_pid" || true
  fi

  sleep 1
  rm -f "$PID_FILE"

  local remain
  remain="$(service_pid_by_port)"
  if [[ -n "$remain" ]]; then
    echo "WARN: service still listening on $PORT (pid=$remain)"
    exit 1
  fi

  echo "Service stopped."
  echo
  port_query
}

restart() {
  stop || true
  start
}

logs() {
  print_header
  if [[ -f "$LOG_FILE" ]]; then
    tail -n 120 "$LOG_FILE"
  else
    echo "No log file yet: $LOG_FILE"
  fi
}

case "$ACTION" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  restart) restart ;;
  logs) logs ;;
  *)
    echo "Usage: $0 [start|stop|status|restart|logs]"
    exit 1
    ;;
esac
