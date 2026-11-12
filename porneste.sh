#!/bin/sh
# Pornește AI-ul local (Ollama), dacă nu rulează deja, apoi aplicația pe portul 8000.
cd "$(dirname "$0")"
OLLAMA="${OLLAMA_BIN:-$HOME/Applications/Ollama.app/Contents/Resources/ollama}"
if [ -x "$OLLAMA" ] && ! curl -s -m 2 http://localhost:11434/api/version >/dev/null 2>&1; then
  echo "Pornesc Ollama..."
  nohup "$OLLAMA" serve > date/ollama.log 2>&1 &
fi
exec .venv/bin/python -W ignore manage.py runserver "${1:-8000}"
