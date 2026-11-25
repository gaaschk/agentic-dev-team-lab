#!/bin/bash
# Set up (first run, or if .venv is missing/incomplete) and start the web UI.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install -q -r requirements-web.txt

# Process substitution (not a pipe) keeps webapp.py as the exec'd process:
# signals reach it directly and the script's exit status is its own.
# -u keeps stdout/stderr unbuffered so webapp.log stays current. tee (no -a)
# starts the log fresh each run instead of growing it without bound.
exec .venv/bin/python -u webapp.py > >(tee webapp.log) 2>&1
