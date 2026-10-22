#!/bin/bash
# Garante Python 3.9+ no Mac (perfil do usuario / Homebrew). Sem sudo.
# Uso: ./instalar-python-mac.sh   ou   ./instalar-python-mac.sh --auto
set -e
AUTO=0
if [ "${1:-}" = "--auto" ] || [ "${1:-}" = "-Auto" ]; then
  AUTO=1
fi

MIN_MAJOR=3
MIN_MINOR=9
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

ok_python() {
  local bin="$1"
  [ -x "$bin" ] || return 1
  "$bin" -c "import sys; raise SystemExit(0 if sys.version_info >= ($MIN_MAJOR, $MIN_MINOR) else 1)" 2>/dev/null
}

# Evita o stub /usr/bin/python3 que so abre dialogo de Command Line Tools
eh_stub_apple() {
  local bin="$1"
  case "$bin" in
    /usr/bin/python3) return 0 ;;
  esac
  return 1
}

escolher_python() {
  local c
  for c in \
    "$HOME/homebrew/bin/python3" \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    "$(command -v python3.12 2>/dev/null || true)" \
    "$(command -v python3.11 2>/dev/null || true)" \
    "$(command -v python3.10 2>/dev/null || true)" \
    "$(command -v python3 2>/dev/null || true)"
  do
    [ -n "$c" ] || continue
    if eh_stub_apple "$c"; then
      continue
    fi
    if ok_python "$c"; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

if [ "$AUTO" -eq 0 ]; then
  echo "Kanban - Python no Mac (sem administrador)"
  echo "Pasta: $ROOT"
  echo "Minimo: ${MIN_MAJOR}.${MIN_MINOR}+  |  Preferencia: Homebrew no usuario"
  echo
fi

if PY="$(escolher_python)"; then
  if [ "$AUTO" -eq 0 ]; then
    echo "Ja existe Python adequado: $("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') ($PY)"
  fi
  exit 0
fi

echo "Python ${MIN_MAJOR}.${MIN_MINOR}+ ainda nao esta pronto. Tentando Homebrew (sem sudo)..."

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew nao encontrado."
  echo "Instale o Homebrew em https://brew.sh (sem precisar deste projeto) ou o Python 3.12+"
  echo "em https://www.python.org/downloads/macos/ e rode de novo o Subir Kanban."
  exit 1
fi

brew install python@3.12 || brew install python3

if PY="$(escolher_python)"; then
  echo "Pronto: $("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') ($PY)"
  exit 0
fi

echo "Nao consegui um Python adequado apos o brew."
exit 1
