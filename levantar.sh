#!/bin/zsh
# Levanta el visor ZackWatch y abre el navegador en http://localhost:8787
cd "$(dirname "$0")"

if curl -s -o /dev/null --max-time 1 http://localhost:8787/api/state; then
  echo "ZackWatch ya está corriendo."
else
  echo "Levantando ZackWatch…"
  nohup .venv/bin/python server.py >> /tmp/zackwatch.log 2>&1 &
  for i in {1..20}; do
    curl -s -o /dev/null --max-time 1 http://localhost:8787/api/state && break
    sleep 0.5
  done
fi

open http://localhost:8787
echo "Visor abierto → http://localhost:8787 (log: /tmp/zackwatch.log)"
