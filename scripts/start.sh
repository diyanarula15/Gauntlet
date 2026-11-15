#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo 'Create .venv and install backend/requirements.txt first; see README.' >&2
  exit 1
fi
if [[ ! -f frontend/dist/index.html ]]; then
  (cd frontend && npm ci && npm run build)
fi
export HARNESS_BASE_URL="${HARNESS_BASE_URL:-http://127.0.0.1:${PORT:-8017}}"
exec .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port "${PORT:-8017}"
