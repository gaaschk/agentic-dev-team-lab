#!/bin/sh
# Set up (first run only) and start the web UI from the project root.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements-web.txt
fi

exec .venv/bin/python webapp.py
