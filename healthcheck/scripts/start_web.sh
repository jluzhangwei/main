#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 not found in PATH"
  exit 1
fi

if ! python3 - <<'PY' >/dev/null 2>&1
import paramiko, yaml, certifi
print(paramiko.__version__, yaml.__version__, certifi.where())
PY
then
  echo "[ERROR] Missing Python dependencies in current interpreter."
  echo "        Run: python3 -m pip install paramiko PyYAML certifi"
  echo "        Python: $(python3 -c 'import sys; print(sys.executable)')"
  exit 1
fi

CA_BUNDLE="$(python3 -c 'import certifi; print(certifi.where())' 2>/dev/null || true)"
if [[ -z "${CA_BUNDLE}" || ! -f "${CA_BUNDLE}" ]]; then
  echo "[ERROR] certifi CA bundle not found. Please run: pip3 install certifi"
  exit 1
fi

export OPENAI_CA_BUNDLE="${CA_BUNDLE}"

echo "[INFO] Python: $(python3 -c 'import sys; print(sys.executable)')"
echo "[INFO] OPENAI_CA_BUNDLE=${OPENAI_CA_BUNDLE}"
echo "[INFO] Starting HealthCheck Web Server..."

exec python3 app/web_server.py
