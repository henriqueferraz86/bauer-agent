#!/bin/bash
set -e

# Sobe o(s) canal(is) de chat (Telegram/Discord) já no boot quando o operador
# já deixou algum habilitado no config.yaml — sem isto, a instalação fica
# "configurada mas muda" até alguém lembrar de clicar Iniciar no painel web
# (o botão nunca roda sozinho; usuários novos não tem como adivinhar isso).
_maybe_start_gateway() {
    if python3 -c "
from bauer.config_loader import load_config
import sys
try:
    cfg = load_config('config.yaml')
    sys.exit(0 if (cfg.telegram.enabled or cfg.discord.enabled) else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
        echo "[bauer] Canal de gateway habilitado no config — iniciando em background..."
        bauer gateway start --background --config config.yaml \
            || echo "[bauer] Aviso: gateway nao iniciou automaticamente (inicie pelo painel web)."
    fi
}

if [ -n "$OLLAMA_HOST" ]; then
    # ── Modo Docker Compose: Ollama já roda como serviço separado ──────────────
    echo "[bauer] Modo Compose — usando Ollama externo: $OLLAMA_HOST"
    # O init baixa o conjunto padrão; quando o operador fixa outro modelo no
    # .env, garanta que ele também exista antes do preflight do serve.
    if [ -n "${BAUER_MODEL:-}" ]; then
        echo "[bauer] Verificando modelo $BAUER_MODEL..."
        ollama pull "$BAUER_MODEL"
    else
        echo "[bauer] Usando o conjunto padrão baixado pelo ollama-init."
    fi
    _maybe_start_gateway
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

    _maybe_start_gateway
    echo "[bauer] Iniciando servidor..."
    exec bauer serve --host 0.0.0.0 --port 8000
fi
