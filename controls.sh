#!/usr/bin/env bash
# Open the lethimcook control panel (macOS / Linux).
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

# tkinter ships with python on Windows/macOS; on Linux it may need a package.
if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
    echo "tkinter is missing. Install it (e.g. 'sudo apt install python3-tk') and rerun."
    exit 1
fi

exec "$PY" scripts/gui.py
