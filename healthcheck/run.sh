#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

WEB_PORT="${WEB_PORT:-8080}"

cleanup_port_processes() {
  local port="$1"
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  fi
  if [[ -z "${pids}" ]]; then
    return 0
  fi
  echo "[INFO] Port ${port} is already in use. Stopping old process(es): ${pids}"
  # shellcheck disable=SC2086
  kill ${pids} 2>/dev/null || true
  sleep 1
  local rest=""
  if command -v lsof >/dev/null 2>&1; then
    rest="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  fi
  if [[ -n "${rest}" ]]; then
    echo "[WARN] Force killing remaining process(es): ${rest}"
    # shellcheck disable=SC2086
    kill -9 ${rest} 2>/dev/null || true
    sleep 1
  fi
}

RELOAD_MODE=1
CHECK_ONLY=0
PASSTHROUGH_ARGS=()
for arg in "$@"; do
  case "${arg}" in
    --no-reload)
      RELOAD_MODE=0
      ;;
    --reload)
      RELOAD_MODE=1
      ;;
    --check)
      CHECK_ONLY=1
      PASSTHROUGH_ARGS+=("--check")
      ;;
    *)
      PASSTHROUGH_ARGS+=("${arg}")
      ;;
  esac
done

if command -v python3.11 >/dev/null 2>&1; then
  PY_BIN="python3.11"
  VENV_DIR=".venv311"
elif command -v python3.10 >/dev/null 2>&1; then
  PY_BIN="python3.10"
  VENV_DIR=".venv310"
elif command -v python3.9 >/dev/null 2>&1; then
  PY_BIN="python3.9"
  VENV_DIR=".venv39"
else
  PY_BIN="python3"
  VENV_DIR=".venv"
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "[INFO] Creating virtualenv: ${VENV_DIR} (${PY_BIN})"
  "${PY_BIN}" -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"

echo "[INFO] Python in use: $(python -c 'import sys; print(sys.executable)')"
python -m pip install --upgrade pip
pip install -r requirements.txt

if [[ "${CHECK_ONLY}" -eq 1 ]]; then
  if [[ ${#PASSTHROUGH_ARGS[@]:-0} -gt 0 ]]; then
    exec ./scripts/start_web.sh "${PASSTHROUGH_ARGS[@]}"
  else
    exec ./scripts/start_web.sh
  fi
fi

# Validate runtime env and SSL chain first.
./scripts/start_web.sh --check
cleanup_port_processes "${WEB_PORT}"

if [[ "${RELOAD_MODE}" -eq 1 ]]; then
  echo "[INFO] Auto-reload enabled. Use --no-reload to disable."
  exec python3 ./scripts/dev_reload.py -- bash -lc "./scripts/start_web.sh"
fi

if [[ ${#PASSTHROUGH_ARGS[@]:-0} -gt 0 ]]; then
  exec ./scripts/start_web.sh "${PASSTHROUGH_ARGS[@]}"
else
  exec ./scripts/start_web.sh
fi
