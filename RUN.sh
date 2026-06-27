#!/usr/bin/env bash
# Tella v1 run wrapper (Mac/Linux).
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo ".venv not found. Run ./SETUP.sh first."
  exit 1
fi

# shellcheck source=/dev/null
. .venv/bin/activate

export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

if [ $# -eq 0 ]; then
  python -m tella
else
  python -m tella "$@"
fi
