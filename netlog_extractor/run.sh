#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

DEFAULT_PORT="${PORT:-8000}"
PORT_SCAN_LIMIT="${PORT_SCAN_LIMIT:-10}"

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

# Auto-inject CA bundle for cloud LLM HTTPS when enterprise trust chain is incomplete.
if [[ -z "${OPENAI_CA_BUNDLE:-}" ]]; then
  CA_BUNDLE="$(python - <<'PY'
try:
    import certifi
    print(certifi.where())
except Exception:
    print("")
PY
)"
  if [[ -n "${CA_BUNDLE}" && -f "${CA_BUNDLE}" ]]; then
    export OPENAI_CA_BUNDLE="${CA_BUNDLE}"
    echo "[INFO] OPENAI_CA_BUNDLE=${OPENAI_CA_BUNDLE}"
  fi
fi

export PYTHONPATH=.

port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  python - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("0.0.0.0", port))
except OSError:
    sys.exit(0)
finally:
    try:
        s.close()
    except Exception:
        pass
sys.exit(1)
PY
}

show_port_owner() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN || true
  else
    echo "[WARN] lsof not found; cannot show port owner details."
  fi
}

choose_port() {
  local start_port="$1"
  local limit="$2"
  local port
  for ((port=start_port; port<start_port+limit; port++)); do
    if ! port_in_use "${port}"; then
      echo "${port}"
      return 0
    fi
    echo "[WARN] Port ${port} is already in use." >&2
    show_port_owner "${port}" >&2
  done
  return 1
}

if ! UVICORN_PORT="$(choose_port "${DEFAULT_PORT}" "${PORT_SCAN_LIMIT}")"; then
  echo "[ERROR] No available port found in range ${DEFAULT_PORT}..$((DEFAULT_PORT + PORT_SCAN_LIMIT - 1))." >&2
  exit 1
fi

if [[ "${UVICORN_PORT}" != "${DEFAULT_PORT}" ]]; then
  echo "[INFO] Port ${DEFAULT_PORT} unavailable, switched to ${UVICORN_PORT}."
else
  echo "[INFO] Using port ${UVICORN_PORT}."
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${UVICORN_PORT}" --reload
