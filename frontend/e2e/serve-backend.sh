#!/usr/bin/env bash
# Backend for the end-to-end test: fresh SQLite database and storage, everything faked.
set -euo pipefail
cd "$(dirname "$0")/../../backend"
rm -rf "$E2E_TMP" && mkdir -p "$E2E_TMP"
export DATABASE_URL="sqlite:///$E2E_TMP/e2e.db"
export STORAGE_ROOT="$E2E_TMP/storage"
export CELERY_ALWAYS_EAGER=true
export NEWSROOM_PROVIDER=fake
export ALIGN_MODEL=fake:silent
export LLM_PROVIDER=fake
export PUBLISHER=fake
export LOG_FORMAT=console
export CORS_ORIGINS=http://localhost:5174
PY=${PYTHON:-.venv/bin/python}
$PY -m alembic upgrade head
exec $PY -m uvicorn app.main:app --port "${API_PORT:-8765}"
