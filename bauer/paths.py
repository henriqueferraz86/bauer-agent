"""Caminhos canônicos do Bauer Agent.

Fonte única de verdade para todos os paths de dados do Bauer.
Override via variável de ambiente BAUER_HOME (útil em testes e CI).

Estrutura padrão em ~/.bauer/:
    config.yaml          — configuração principal
    .env                 — variáveis de ambiente (tokens, chaves)
    .runtime_state.json  — estado do preflight/doctor
    memory/              — arquivos de aprendizado (MODEL_EXPERIENCE.md, etc.)
    logs/                — logs dos serviços (gateway, runtime, daemon)
    workspace/           — sandbox de trabalho do agente
"""

from __future__ import annotations

import os
from pathlib import Path


def get_bauer_home() -> Path:
    """Retorna o diretório home do Bauer (~/.bauer/ ou $BAUER_HOME).

    Cria o diretório se não existir.
    """
    override = os.environ.get("BAUER_HOME")
    home = Path(override).expanduser() if override else Path.home() / ".bauer"
    home.mkdir(parents=True, exist_ok=True)
    return home


def config_path() -> Path:
    """Caminho canônico do config.yaml."""
    return get_bauer_home() / "config.yaml"


def memory_dir() -> Path:
    """Diretório de memória (MODEL_EXPERIENCE.md, FEEDBACK.md, etc.)."""
    d = get_bauer_home() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def runtime_state_path() -> Path:
    """Caminho do .runtime_state.json."""
    return get_bauer_home() / ".runtime_state.json"


def logs_dir() -> Path:
    """Diretório de logs dos serviços."""
    d = get_bauer_home() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def workspace_dir() -> Path:
    """Sandbox de trabalho do agente."""
    d = get_bauer_home() / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d


def loop_skills_dir() -> Path:
    """Diretório de loop-skills instaladas pelo usuário (YAML, opt-in).

    Instalar um arquivo aqui é o único jeito de habilitar o auto-gatilho
    do `/loop` — diretório vazio = recurso é um no-op completo.
    """
    d = get_bauer_home() / "loop_skills"
    d.mkdir(parents=True, exist_ok=True)
    return d
