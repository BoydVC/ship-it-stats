#!/usr/bin/env bash
# Start Ship-it Stats in the background (idempotent): collect fresh stats, then serve on :4260.
cd "$(dirname "$0")"
PORT=${SHIPIT_PORT:-4260}
python3 collect.py
if curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  echo "Ship-it Stats already running: http://127.0.0.1:$PORT"; exit 0
fi
setsid nohup python3 server.py > data/server.log 2>&1 &
for i in $(seq 1 20); do curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 0.3; done
echo "Ship-it Stats started (pid $!): http://127.0.0.1:$PORT"
