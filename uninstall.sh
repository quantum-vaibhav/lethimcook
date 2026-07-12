#!/usr/bin/env bash
# One-click uninstall for lethimcook (macOS / Linux)
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "Python 3 is required to run the uninstaller."
    exit 1
fi

exec "$PY" setup.py --uninstall
