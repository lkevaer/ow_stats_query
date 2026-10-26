#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [[ -f api_server.pid ]] && kill -0 "$(cat api_server.pid)" 2>/dev/null; then
  echo "Already running; use stop_api.sh first."
  exit 0
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements_for_api.txt
host="${OW_SERVER_HOST:-127.0.0.1}"
port="${OW_SERVER_PORT:-41888}"
env_args=()
if [[ -f .env ]]; then env_args=(--env-file .env); fi
nohup .venv/bin/python -m uvicorn api_server.main:app \
  --host "$host" --port "$port" --workers 1 --loop asyncio \
  --limit-concurrency 64 --timeout-graceful-shutdown 30 \
  "${env_args[@]}" > api_server.log 2>&1 &
echo "$!" > api_server.pid
echo "Started: http://$host:$port/"
echo "Check api_server.log for readiness."
