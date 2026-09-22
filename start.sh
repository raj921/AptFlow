#!/bin/sh
set -eu

: "${PORT:=8080}"

python3 -m uvicorn main:app --host 127.0.0.1 --port 8000 &
api_pid=$!

cleanup() {
  kill "$api_pid" 2>/dev/null || true
  wait "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

exec npm run start -- --hostname 0.0.0.0 --port "$PORT"
