#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [[ ! -f api_server.pid ]]; then echo "No saved server PID."; exit 0; fi
server_pid="$(cat api_server.pid)"
if [[ ! "$server_pid" =~ ^[0-9]+$ ]]; then echo "Invalid saved PID."; exit 1; fi
if kill -0 "$server_pid" 2>/dev/null; then
  server_command="$(ps -p "$server_pid" -o args=)"
  if [[ "$server_command" != *"uvicorn api_server.main:app"* ]]; then
    echo "PID belongs to another process; nothing was stopped."
    exit 1
  fi
  kill "$server_pid"
  echo "Stopped server PID $server_pid."
else
  echo "Server is already stopped."
fi
rm -f api_server.pid
