#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

if [[ -d ".venv311" ]]; then
  source .venv311/bin/activate
elif [[ -d ".venv310" ]]; then
  source .venv310/bin/activate
elif [[ -d ".venv" ]]; then
  source .venv/bin/activate
else
  echo "No virtualenv found. Run ./run.sh first."
  exit 1
fi

export PYTHONPATH=.
exec pytest -q
