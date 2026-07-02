"""Smoke tests do agents.yaml REAL na raiz do projeto — os 10 especialistas
de propósito geral usados por delegate_task (bauer/tools/execution.py) e
listados no system prompt quando `agent.specialist_delegation` está ligado.

Não é um teste de unidade isolado de propósito (usa o arquivo real do repo,
não um registry temporário) — o objetivo é travar que o arquivo continua
parseando e que os nomes esperados continuam presentes, para não quebrar em
silêncio se alguém editar agents.yaml manualmente.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENTS_FILE = _REPO_ROOT / "agents.yaml"

_EXPECTED_SPECIALISTS = {
    "code-specialist",
    "devops-specialist",
    "security-specialist",
    "data-specialist",
    "research-specialist",
    "writing-specialist",
    "sre-specialist",
    "design-specialist",
    "finance-specialist",
    "productivity-specialist",
}


def test_agents_yaml_exists_and_parses():
    assert _AGENTS_FILE.exists(), f"agents.yaml não encontrado em {_AGENTS_FILE}"
    from bauer.agent_registry import AgentRegistry
    reg = AgentRegistry(str(_AGENTS_FILE))
    agents = reg.list_agents()
    assert len(agents) >= len(_EXPECTED_SPECIALISTS)


def test_all_expected_specialists_present():
    from bauer.agent_registry import AgentRegistry
    reg = AgentRegistry(str(_AGENTS_FILE))
    names = {a.name for a in reg.list_agents()}
    missing = _EXPECTED_SPECIALISTS - names
    assert not missing, f"especialistas faltando em agents.yaml: {missing}"


def test_specialists_have_no_url_local():
    """Os 10 especialistas de área são locais (delegate_task aplica o system
    prompt deles diretamente) — url é exclusivo de workers remotos como
    worker-remoto."""
    from bauer.agent_registry import AgentRegistry
    reg = AgentRegistry(str(_AGENTS_FILE))
    for name in _EXPECTED_SPECIALISTS:
        ag = reg.get(name)
        assert ag is not None
        assert not ag.url, f"{name} não deveria ter url (é um especialista local)"
        assert ag.system.strip(), f"{name} sem system prompt"
        assert ag.description.strip(), f"{name} sem description"


def test_match_finds_reasonable_specialist_per_area():
    """Smoke test de precisão do auto_select para tarefas representativas de
    cada área — não cobre todo caso possível, só confirma que o registry real
    (não um mock) continua roteando corretamente após qualquer edição futura."""
    from bauer.agent_registry import AgentRegistry
    reg = AgentRegistry(str(_AGENTS_FILE))

    cases = [
        ("configurar Docker e pipeline de CI/CD", "devops-specialist"),
        ("revisar vulnerabilidades OWASP no login", "security-specialist"),
        ("treinar um modelo de classificacao", "data-specialist"),
        ("pesquisar e comparar as melhores opcoes de banco de dados", "research-specialist"),
        ("revisar e traduzir a documentacao", "writing-specialist"),
        ("criar runbook de incidente e SLOs", "sre-specialist"),
        ("prototipar wireframe no Figma", "design-specialist"),
        ("montar modelo financeiro DCF", "finance-specialist"),
        ("planejar cronograma e orcamento do projeto", "productivity-specialist"),
        ("refatorar essa funcao e adicionar testes", "code-specialist"),
    ]
    for task, expected in cases:
        result = reg.match(task)
        assert result is not None, f"nenhum match para {task!r} (esperado {expected})"
        assert result.name == expected, (
            f"{task!r} -> {result.name}, esperado {expected}"
        )


def test_noise_tasks_do_not_match_any_specialist():
    """Conversa casual não deve disparar delegação — auto_select deve
    devolver None para tarefas sem relação com nenhuma área."""
    from bauer.agent_registry import AgentRegistry
    reg = AgentRegistry(str(_AGENTS_FILE))

    for task in ("oi, tudo bem?", "que horas sao agora?"):
        result = reg.match(task)
        assert result is None, f"{task!r} deu match indevido: {result}"
