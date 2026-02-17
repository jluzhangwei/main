#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

CHECK_ONLY=0
if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=1
fi

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

PYTHON_BIN="$(python3 -c 'import sys; print(sys.executable)')"
SSL_PATHS="$(python3 -c "import ssl; p=ssl.get_default_verify_paths(); print(f'cafile={p.cafile}; capath={p.capath}')")"
CERTIFI_VER="$(python3 -c 'import certifi; print(getattr(certifi, "__version__", "unknown"))' 2>/dev/null || echo "unknown")"

if [[ -z "${OPENAI_SSL_TRUST_STORE:-}" ]]; then
  export OPENAI_SSL_TRUST_STORE="system"
fi

if [[ -n "${OPENAI_CA_BUNDLE:-}" ]]; then
  if [[ ! -f "${OPENAI_CA_BUNDLE}" ]]; then
    echo "[WARN] OPENAI_CA_BUNDLE is set but file not found: ${OPENAI_CA_BUNDLE}"
    echo "[WARN] Fallback to certifi bundle."
    export OPENAI_CA_BUNDLE="${CA_BUNDLE}"
  fi
else
  export OPENAI_CA_BUNDLE="${CA_BUNDLE}"
fi

echo "[INFO] Python: ${PYTHON_BIN}"
echo "[INFO] certifi version: ${CERTIFI_VER}"
echo "[INFO] SSL defaults: ${SSL_PATHS}"
echo "[INFO] OPENAI_SSL_TRUST_STORE=${OPENAI_SSL_TRUST_STORE}"
echo "[INFO] OPENAI_CA_BUNDLE=${OPENAI_CA_BUNDLE}"
if [[ "${OPENAI_SSL_NO_VERIFY:-}" =~ ^(1|true|yes|on)$ ]]; then
  echo "[WARN] OPENAI_SSL_NO_VERIFY is enabled. TLS cert verification is DISABLED."
fi

echo "[INFO] Certificate self-check:"
python3 - <<'PY'
import os, ssl
from pathlib import Path
bundle = os.environ.get("OPENAI_CA_BUNDLE", "").strip()
ok = bool(bundle and Path(bundle).is_file())
print(f"  - OPENAI_CA_BUNDLE exists: {ok}")
if ok:
    print(f"  - Bundle path: {bundle}")
ctx = ssl.create_default_context(cafile=bundle if ok else None)
print(f"  - SSL context created: {bool(ctx)}")
PY

if [[ "${CHECK_ONLY}" -eq 1 ]]; then
  echo "[INFO] Check-only mode done."
  exit 0
fi

echo "[INFO] Starting HealthCheck Web Server on http://0.0.0.0:8080 ..."

exec python3 app/web_server.py
