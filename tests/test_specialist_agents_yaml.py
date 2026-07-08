"""Smoke tests dos 10 especialistas de propósito geral embutidos no pacote
(bauer/data/agents/specialists.yaml), usados por delegate_task
(bauer/tools/execution.py) e listados no system prompt quando
`agent.specialist_delegation` está ligado.

Não são testes de unidade isolados de propósito (usam o arquivo real
embutido no pacote, não um registry temporário) — o objetivo é travar que o
arquivo continua parseando e que os nomes esperados continuam presentes,
para não quebrar em silêncio se alguém editar o YAML manualmente. O arquivo
é carregado por PATH DO PACOTE (Path(__file__).parent em agent_registry.py),
não pelo cwd — funciona independente de onde os testes rodam.
"""

from __future__ import annotations

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


def test_builtin_specialists_file_parses():
    from bauer.agent_registry import list_builtin_specialists

    agents = list_builtin_specialists()
    assert len(agents) >= len(_EXPECTED_SPECIALISTS)


def test_all_expected_specialists_present():
    from bauer.agent_registry import list_builtin_specialists

    names = {a.name for a in list_builtin_specialists()}
    missing = _EXPECTED_SPECIALISTS - names
    assert not missing, f"especialistas faltando em specialists.yaml: {missing}"


def test_specialists_have_no_url_local():
    """Os 10 especialistas de área são locais (delegate_task aplica o system
    prompt deles diretamente) — url é exclusivo de workers remotos como
    worker-remoto (definido no agents.yaml do usuário, não aqui)."""
    from bauer.agent_registry import list_builtin_specialists

    by_name = {a.name: a for a in list_builtin_specialists()}
    for name in _EXPECTED_SPECIALISTS:
        ag = by_name.get(name)
        assert ag is not None
        assert not ag.url, f"{name} não deveria ter url (é um especialista local)"
        assert ag.system.strip(), f"{name} sem system prompt"
        assert ag.description.strip(), f"{name} sem description"


def test_match_finds_reasonable_specialist_per_area():
    """Smoke test de precisão do match_agents() para tarefas representativas
    de cada área — não cobre todo caso possível, só confirma que o arquivo
    embutido real (não um mock) continua roteando corretamente após
    qualquer edição futura."""
    from bauer.agent_registry import list_builtin_specialists, match_agents

    agents = list_builtin_specialists()
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
        result = match_agents(task, agents)
        assert result is not None, f"nenhum match para {task!r} (esperado {expected})"
        assert result.name == expected, (
            f"{task!r} -> {result.name}, esperado {expected}"
        )


def test_noise_tasks_do_not_match_any_specialist():
    """Conversa casual não deve disparar delegação — match_agents() deve
    devolver None para tarefas sem relação com nenhuma área."""
    from bauer.agent_registry import list_builtin_specialists, match_agents

    agents = list_builtin_specialists()
    for task in ("oi, tudo bem?", "que horas sao agora?"):
        result = match_agents(task, agents)
        assert result is None, f"{task!r} deu match indevido: {result}"


def test_merged_specialist_pool_includes_builtins_with_no_user_file(tmp_path):
    """merged_specialist_pool() traz os embutidos mesmo sem nenhum agents.yaml
    do usuário existir — é exatamente o cenário 'bauer agent rodado de uma
    pasta qualquer' que motivou mover os especialistas pro pacote."""
    from bauer.agent_registry import merged_specialist_pool

    pool = merged_specialist_pool(str(tmp_path / "nao-existe.yaml"))
    names = {a.name for a in pool}
    assert _EXPECTED_SPECIALISTS <= names


def test_merged_specialist_pool_user_agent_overrides_builtin_by_name(tmp_path):
    import yaml

    agents_file = tmp_path / "agents.yaml"
    agents_file.write_text(
        yaml.dump({"agents": [{
            "name": "devops-specialist",
            "description": "versao customizada do usuario",
            "system": "custom",
        }]}, allow_unicode=True),
        encoding="utf-8",
    )
    from bauer.agent_registry import merged_specialist_pool

    pool = merged_specialist_pool(str(agents_file))
    by_name = {a.name: a for a in pool}
    assert by_name["devops-specialist"].description == "versao customizada do usuario"
    # os outros 9 embutidos continuam presentes, só o nome sobrescrito muda
    assert len(pool) == len(_EXPECTED_SPECIALISTS)
