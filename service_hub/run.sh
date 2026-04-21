#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

HOST="${HUB_HOST:-127.0.0.1}"
PORT="${HUB_PORT:-18888}"
RELOAD=0
FOREGROUND=0
ACTION="start"

STATE_DIR="${ROOT_DIR}/state"
LOG_DIR="${ROOT_DIR}/logs"
PID_FILE="${STATE_DIR}/service_hub.pid"
OUT_LOG="${LOG_DIR}/service_hub.out.log"

mkdir -p "${STATE_DIR}" "${LOG_DIR}"

for arg in "$@"; do
  case "${arg}" in
    start|stop|restart|status|logs)
      ACTION="${arg}"
      ;;
    --reload)
      RELOAD=1
      FOREGROUND=1
      ;;
    --foreground|--fg)
      FOREGROUND=1
      ;;
    *)
      echo "[ERROR] Unknown argument: ${arg}" >&2
      echo "Usage: ./run.sh [start|stop|restart|status|logs] [--foreground|--reload]" >&2
      exit 1
      ;;
  esac
done

if command -v python3.11 >/dev/null 2>&1; then
  PY_BIN="python3.11"
  VENV_DIR=".venv311"
elif command -v python3.10 >/dev/null 2>&1; then
  PY_BIN="python3.10"
  VENV_DIR=".venv310"
else
  PY_BIN="python3"
  VENV_DIR=".venv"
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  "${PY_BIN}" -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1090
source "${VENV_DIR}/bin/activate"

ensure_deps() {
  python -m pip install --upgrade pip >/dev/null
  pip install -r requirements.txt >/dev/null
}

pid_is_alive() {
  local pid="$1"
  if [[ -z "${pid}" ]]; then
    return 1
  fi
  kill -0 "${pid}" >/dev/null 2>&1
}

find_port_pids() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true
  fi
}

stop_running() {
  local pid=""
  if [[ -f "${PID_FILE}" ]]; then
    pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  fi

  if pid_is_alive "${pid}"; then
    echo "[INFO] Stopping Service Hub pid=${pid}"
    kill "${pid}" >/dev/null 2>&1 || true
    sleep 1
    if pid_is_alive "${pid}"; then
      kill -9 "${pid}" >/dev/null 2>&1 || true
    fi
  fi

  local pids
  pids="$(find_port_pids)"
  if [[ -n "${pids}" ]]; then
    echo "[INFO] Releasing port ${PORT}: ${pids}"
    # shellcheck disable=SC2086
    kill ${pids} >/dev/null 2>&1 || true
    sleep 1
    pids="$(find_port_pids)"
    if [[ -n "${pids}" ]]; then
      # shellcheck disable=SC2086
      kill -9 ${pids} >/dev/null 2>&1 || true
    fi
  fi

  for _ in $(seq 1 30); do
    if [[ -z "$(find_port_pids)" ]]; then
      break
    fi
    sleep 0.2
  done

  rm -f "${PID_FILE}"
}

status() {
  local pid=""
  if [[ -f "${PID_FILE}" ]]; then
    pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  fi
  local pids
  pids="$(find_port_pids)"
  if pid_is_alive "${pid}" || [[ -n "${pids}" ]]; then
    echo "running"
    [[ -n "${pid}" ]] && echo "pid=${pid}"
    [[ -n "${pids}" ]] && echo "port_pids=${pids}"
    return 0
  fi
  echo "stopped"
  return 1
}

start_daemon() {
  if status >/dev/null 2>&1; then
    echo "[INFO] Service Hub already running."
    return 0
  fi
  ensure_deps
  echo "[INFO] Starting Service Hub daemon on http://${HOST}:${PORT}"
  if command -v setsid >/dev/null 2>&1; then
    nohup setsid uvicorn app:app --host "${HOST}" --port "${PORT}" >>"${OUT_LOG}" 2>&1 < /dev/null &
  else
    nohup uvicorn app:app --host "${HOST}" --port "${PORT}" >>"${OUT_LOG}" 2>&1 < /dev/null &
  fi
  disown || true
  local pid=$!
  echo "${pid}" > "${PID_FILE}"

  local ok=0
  for _ in $(seq 1 40); do
    if ! pid_is_alive "${pid}"; then
      ok=0
      break
    fi
    if curl -s -m 1 "http://127.0.0.1:${PORT}/login" >/dev/null 2>&1; then
      ok=1
      break
    fi
    sleep 0.25
  done
  if [[ "${ok}" -ne 1 ]]; then
    echo "[ERROR] Service Hub failed to start. Check ${OUT_LOG}" >&2
    tail -n 40 "${OUT_LOG}" >&2 || true
    stop_running
    exit 1
  fi
  echo "[OK] Service Hub started. pid=${pid}, log=${OUT_LOG}"
}

start_foreground() {
  ensure_deps
  UVICORN_ARGS=("app:app" "--host" "${HOST}" "--port" "${PORT}")
  if [[ "${RELOAD}" -eq 1 ]]; then
    UVICORN_ARGS+=("--reload")
  fi
  exec uvicorn "${UVICORN_ARGS[@]}"
}

case "${ACTION}" in
  start)
    if [[ "${FOREGROUND}" -eq 1 ]]; then
      start_foreground
    else
      start_daemon
    fi
    ;;
  stop)
    stop_running
    echo "[OK] Service Hub stopped."
    ;;
  restart)
    stop_running
    if [[ "${FOREGROUND}" -eq 1 ]]; then
      start_foreground
    else
      start_daemon
    fi
    ;;
  status)
    status
    ;;
  logs)
    if [[ -f "${OUT_LOG}" ]]; then
      tail -n 120 "${OUT_LOG}"
    else
      echo "[INFO] No log file yet: ${OUT_LOG}"
    fi
    ;;
esac
