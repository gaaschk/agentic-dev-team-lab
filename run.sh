#!/bin/sh
# Set up (first run, or if .venv is missing/incomplete) and start the web UI.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install -q -r requirements-web.txt

exec .venv/bin/python webapp.py
