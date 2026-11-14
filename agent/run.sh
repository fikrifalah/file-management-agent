#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "[setup] Creating virtual environment in agent/.venv..."
    python3 -m venv .venv
    echo "[setup] Installing dependencies..."
    .venv/bin/python -m pip install --upgrade pip -q
    .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/python main.py "$@"
