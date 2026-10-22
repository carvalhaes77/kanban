#!/bin/bash
# Duplo clique no Finder (Mac). Mesmo criterio do subir-kanban.cmd no Windows.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
URL="http://127.0.0.1:8765/kanban-ti-visivel-demo/"
LOG="$ROOT/kanban-web.log"

echo
echo "Preparando o Kanban neste Mac..."
echo "(Se faltar Python, tenta Homebrew no seu usuario — sem administrador.)"
echo

if lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  open "$URL"
  echo "Kanban ja estava no ar — sem nova instancia. Aberto no navegador."
  echo "Para encerrar: botao Desligar no quadro, ou Parar-Kanban.command"
  sleep 3
  exit 0
fi

chmod +x "$ROOT/instalar-python-mac.sh" "$ROOT/Subir-Kanban.command" "$ROOT/Parar-Kanban.command" 2>/dev/null || true

if ! "$ROOT/instalar-python-mac.sh" --auto; then
  echo
  echo "Nao deu para preparar o Python sozinho."
  echo "Opcao: instale Python 3.12+ (python.org ou brew) e rode de novo."
  echo "O individual continua abrindo so o HTML, sem Python."
  read -r -p "Enter para fechar..."
  exit 1
fi

PY=""
for c in \
  "$HOME/homebrew/bin/python3" \
  /opt/homebrew/bin/python3 \
  /usr/local/bin/python3 \
  "$(command -v python3.12 2>/dev/null || true)" \
  "$(command -v python3 2>/dev/null || true)"
do
  [ -n "$c" ] && [ -x "$c" ] || continue
  case "$c" in /usr/bin/python3) continue ;; esac
  if "$c" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" 2>/dev/null; then
    PY="$c"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "Python instalado, mas este Terminal ainda nao o enxerga. Feche e abra de novo o Subir-Kanban."
  read -r -p "Enter para fechar..."
  exit 1
fi

echo "[$(date)] Iniciando $PY" > "$LOG"
nohup "$PY" -u "$ROOT/servidor-local.py" >> "$LOG" 2>&1 &

for i in $(seq 1 25); do
  if lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
    open "$URL"
    echo
    echo "Pronto. Kanban aberto no navegador."
    echo "Compartilhado: Parametros → Gerar chave e ativar → Copiar chave."
    echo "Para encerrar: Parar-Kanban.command"
    sleep 4
    exit 0
  fi
  sleep 1
done

echo "Nao subiu na porta 8765. Veja: $LOG"
tail -n 40 "$LOG" 2>/dev/null || true
read -r -p "Enter para fechar..."
exit 1
