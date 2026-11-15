#!/usr/bin/env bash
# Start both services, wait for them to accept connections, then exit leaving
# them running in the background. Logs land in logs/.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PY:-}"
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
fi
mkdir -p logs

"$PY" account_server.py     > logs/account.log     2>&1 & echo $! > logs/account.pid
"$PY" transaction_server.py > logs/transaction.log 2>&1 & echo $! > logs/transaction.pid

for port in 50051 50052; do
  for _ in $(seq 1 50); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then break; fi
    sleep 0.1
  done
done

echo "account_server     pid $(cat logs/account.pid)     :50051"
echo "transaction_server pid $(cat logs/transaction.pid) :50052"
