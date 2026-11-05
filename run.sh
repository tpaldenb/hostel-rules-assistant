#!/bin/sh
# Starts the app at http://localhost:8123, setting it up first if needed.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/uvicorn ]; then
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
[ -f .env ] || cp .env.example .env
echo "Open http://localhost:8123 (press Ctrl+C to stop)"
exec .venv/bin/uvicorn app.main:app --port 8123
