#!/usr/bin/env bash
# Builds and starts the stack, loads the synthetic demo dataset and runs the full pipeline.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${CIVALPHA_PORT:-8088}"
docker compose up -d --build
echo "waiting for the backend..."
for _ in $(seq 1 60); do curl -fs "http://localhost:$PORT/api/meta" >/dev/null && break; sleep 3; done
curl -fs -X POST "http://localhost:$PORT/api/admin/demo/load" >/dev/null
echo "demo load started; following the job log"
for _ in $(seq 1 120); do
  status=$(curl -fs "http://localhost:$PORT/api/admin/jobs" | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["status"])')
  [ "$status" != "RUNNING" ] && break
  sleep 5
done
curl -fs "http://localhost:$PORT/api/admin/jobs" | python3 -c 'import sys,json; j=json.load(sys.stdin)[0]; print(j["status"]); print(j["log"])'
echo "open http://localhost:$PORT"
