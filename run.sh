#!/usr/bin/env sh
# Start SiteForge on http://localhost:8000
cd "$(dirname "$0")/backend" && exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" "$@"
