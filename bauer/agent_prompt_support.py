"""Contexto adicional opcional para o system prompt do agente."""

from __future__ import annotations


def specs_section(format_hint: str, specs_dir: str = "specs") -> str:
    try:
        from .spec_manager import SpecManager
        context = SpecManager(specs_dir).specs_context(compact=True)
        return f"{format_hint}\n\n{context}" if context else format_hint
    except Exception:  # noqa: BLE001 -- specs são enriquecimento opcional
        return format_hint


def minimal_code_mode_enabled() -> bool:
    try:
        from .config_loader import load_config
        return load_config().agent.minimal_code_mode
    except Exception:  # noqa: BLE001 -- default seguro do contrato de config
        return True


def specialists_block() -> str:
    try:
        from .config_loader import load_config
        if not load_config().agent.specialist_delegation:
            return ""
    except Exception:  # noqa: BLE001 -- segue o default True do config
        pass
    try:
        from .agent_registry import merged_specialist_pool, resolve_user_agents_path
        agents = [agent for agent in merged_specialist_pool(str(resolve_user_agents_path())) if not agent.url]
        if not agents:
            return ""
        intro = (
            "# ESPECIALISTAS DISPONIVEIS\n"
            "Estes agents tem system prompt ajustado para suas areas, mas "
            "delegate_task(agent_name=\"<nome>\", task=\"...\") pra eles e uma "
            "consulta de UMA RESPOSTA EM TEXTO — SEM tools, sem acesso a "
            "arquivos/shell/docker, sem multiplas rodadas. Use SOMENTE para "
            "pedir uma opiniao/analise pontual (revisar um trecho, explicar um "
            "conceito, comparar opcoes, redigir um texto) que cabe numa resposta "
            "unica.\n"
            "NUNCA delegue tarefas que precisam EXECUTAR algo (rodar comando, "
            "subir/parar servico, ler logs, editar arquivo, navegar pasta) — "
            "mesmo que a tarefa seja da area de um especialista (ex.: 'suba o "
            "docker e arrume o dashboard' e DevOps mas exige tools reais: faca "
            "voce mesmo com run_command/read_file/write_file, NAO delegue). "
            "Na duvida entre delegar ou executar, execute voce mesmo."
        )
        return "\n" + "\n".join([intro, *(f"  - {agent.name}: {agent.description}" for agent in agents)])
    except Exception:  # noqa: BLE001 -- registry é enriquecimento opcional
        return ""
