#!/usr/bin/env bash
# Tella v1 setup script (Mac/Linux).
set -euo pipefail

cd "$(dirname "$0")"

echo "=== Tella SETUP ==="
echo

# 1. Check Python 3.12+
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.12+ from https://www.python.org/"
  exit 1
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]; }; then
  echo "ERROR: Python 3.12+ required, found $PY_VER"
  exit 1
fi

# 2. Create .venv if missing
if [ ! -d .venv ]; then
  echo "Creating .venv ..."
  python3 -m venv .venv
fi

# 3. Activate + install
echo "Installing dependencies ..."
# shellcheck source=/dev/null
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .

# 4. Hint about .env
if [ ! -f .env ]; then
  echo
  echo "NOTE: .env not found."
  echo "Copy .env.example to .env and fill in your keys:"
  echo "  - GEMINI_API_KEY  (https://aistudio.google.com/apikey)"
  echo "  - CF_ACCOUNTS or CF_ACCOUNT_ID + CF_AI_TOKEN  (AI image mode)"
  echo "  - PEXELS_API_KEY  (stock photo / video mode)"
  echo "  - GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_TTS_API_KEY  (TTS)"
fi

echo
echo "=== SETUP DONE ==="
echo "Run with: ./RUN.sh"
