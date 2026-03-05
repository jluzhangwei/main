#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

HOST="${HUB_HOST:-127.0.0.1}"
PORT="${HUB_PORT:-18888}"
RELOAD=0

for arg in "$@"; do
  case "${arg}" in
    --reload)
      RELOAD=1
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

source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip
pip install -r requirements.txt

UVICORN_ARGS=("app:app" "--host" "${HOST}" "--port" "${PORT}")
if [[ "${RELOAD}" -eq 1 ]]; then
  UVICORN_ARGS+=("--reload")
fi

exec uvicorn "${UVICORN_ARGS[@]}"
