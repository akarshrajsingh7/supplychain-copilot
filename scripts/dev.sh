#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -d .venv || ! -d frontend/node_modules ]]; then
  echo 'Install dependencies first: uv sync --frozen && (cd frontend && npm ci)'
  exit 1
fi
uv run --frozen python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000 &
copilot_backend_pid=$!
(cd frontend && npm run dev) &
copilot_frontend_pid=$!
cleanup() { kill "$copilot_backend_pid" "$copilot_frontend_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
echo 'SupplyChain Copilot: http://127.0.0.1:5173 — Ctrl-C stops both servers.'
wait "$copilot_backend_pid" "$copilot_frontend_pid"
