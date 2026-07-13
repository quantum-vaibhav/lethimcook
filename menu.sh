#!/usr/bin/env bash
# Open the lethimcook terminal menu (macOS / Linux).
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "Python 3 is required. Install it with your package manager (apt/brew/dnf) and rerun."
    exit 1
fi

exec "$PY" scripts/menu.py
