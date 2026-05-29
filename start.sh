#!/bin/bash
set -e

if [ -n "$OLLAMA_HOST" ]; then
    # ── Modo Docker Compose: Ollama já roda como serviço separado ──────────────
    echo "[bauer] Modo Compose — usando Ollama externo: $OLLAMA_HOST"
    echo "[bauer] Iniciando servidor..."
    exec bauer serve --host 0.0.0.0 --port 8000
else
    # ── Modo standalone: sobe Ollama localmente ────────────────────────────────
    echo "[bauer] Modo standalone — iniciando Ollama em background..."
    ollama serve &

    echo "[bauer] Aguardando Ollama ficar disponivel..."
    until python3 -c "
import urllib.request, sys
try:
    urllib.request.urlopen('http://localhost:11434/api/tags', timeout=2)
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; do
        sleep 2
    done
    echo "[bauer] Ollama pronto."

    echo "[bauer] Verificando modelo ${BAUER_MODEL:-qwen3:0.6b}..."
    ollama pull "${BAUER_MODEL:-qwen3:0.6b}"
    echo "[bauer] Modelo pronto."

    echo "[bauer] Iniciando servidor..."
    exec bauer serve --host 0.0.0.0 --port 8000
fi
