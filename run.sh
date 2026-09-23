#!/usr/bin/env bash
# Start the job board at http://localhost:${PORT:-8000}. Kev (if selected in .env) loads on demand.
set -euo pipefail
cd "$(dirname "$0")"
PY="../.venv/bin/python"
[ -x "$PY" ] || { python3 -m venv ../.venv; PY="../.venv/bin/python"; }
"$PY" -m pip install -q -r requirements.txt 2>/dev/null || true
[ -f .env ] || cp .env.example .env
set -a; . ./.env; set +a

# The local engine (Kev) is started on demand by the app itself and unloaded when idle (app/engine.py).

"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}" "$@"
