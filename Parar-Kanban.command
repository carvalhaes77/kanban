#!/bin/bash
# Encerra web (8765) e compartilhado (8777) no Mac.
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

matar_porta() {
  local porta="$1"
  local pids
  pids="$(lsof -nP -iTCP:"$porta" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    echo "Nada escutando na porta $porta."
    return
  fi
  echo "Encerrando na porta $porta: $pids"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 1
  pids="$(lsof -nP -iTCP:"$porta" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}

matar_porta 8765
matar_porta 8777
echo
echo "Kanban (e compartilhado, se estava ligado) parado."
sleep 2
