#!/bin/bash
# Benchmark the remaining engines one at a time (16 GB RAM: never two models loaded at once).
cd "$(dirname "$0")/.."
PY=../.venv/bin/python
E=bench/engines
SMOKE='{"model":"jev-latest","state":"My payouts failed for 3 days.","questions":{"u":{"type":"noul","instructions":"Is this urgent?"}}}'

wait_ready() {  # port, log
  for i in $(seq 1 240); do
    curl -s -m 60 "localhost:$1/v1/systemone" -H 'content-type: application/json' -d "$SMOKE" | grep -q '"answers"' && return 0
    sleep 5
  done
  echo "server on $1 never became ready"; tail -20 "$2"; return 1
}

bench() {  # name, port, serverlog, start-command...
  local name=$1 port=$2 log=$3; shift 3
  echo "=== $name  $(date +%T)"
  ( "$@" > "$log" 2>&1 & echo $! > "$E/$name.pid" )
  if wait_ready "$port" "$log"; then
    $PY bench/run.py --resume --name "$name" --base-url "http://127.0.0.1:$port" > "bench/results/$name.log" 2>&1
    tail -1 "bench/results/$name.log"
  fi
  pkill -P "$(cat $E/$name.pid)" 2>/dev/null; kill "$(cat $E/$name.pid)" 2>/dev/null; sleep 5
  pkill -f "port $port" 2>/dev/null; sleep 5
}

bench kev-4b 8011 $E/kev-4b.log bash -c "cd $E/kev && exec .venv/bin/python -m kev.serve --run jaredpalmer/kev-4b --port 8011"
bench kev-0.8b 8013 $E/kev-0.8b.log bash -c "cd $E/kev && exec .venv/bin/python -m kev.serve --run jaredpalmer/kev-0.8b --port 8013"

while ! grep -q OPENJEV_W_OK $E/setup.log; do sleep 30; done
bench open-jev-gemma3-4b 8012 $E/open-jev.log bash -c "cd $E/open-jev && exec .venv/bin/openjev serve --host 127.0.0.1 --port 8012 --model models/gemma-3-4b-it"

$PY bench/report.py
echo "ALL BENCHMARKS DONE $(date +%T)"
