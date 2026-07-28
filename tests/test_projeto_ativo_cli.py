"""Projeto ATIVO controlável pela CLI, e sincronizado ao entrar na pasta.

`set_active()` tinha UM call site em todo o codebase — `desktop_api.py`, o
botão da tela Projetos. Pela CLI não havia como trocar o projeto ativo, e o
`bauer agent` detectava a pasta, usava como workspace da sessão e deixava o
ponteiro onde estava. Resultado: CLI trabalhando num projeto e Desktop
apontando para outro, em silêncio.
"""

from __future__ import annotations

import json

import pytest

from bauer import projects_registry as pr
from bauer.commands.project_cmd import _resolver_projeto


@pytest.fixture
def registro(tmp_path, monkeypatch):
    """Registro com dois projetos; o primeiro ativo."""
    home = tmp_path / "bauer_home"
    home.mkdir()
    monkeypatch.setenv("BAUER_HOME", str(home))

    for nome in ("github-radar", "forex-ia"):
        d = tmp_path / "projetos" / nome
        d.mkdir(parents=True)
        pr.add_project(d)

    todos = pr.list_projects(enrich=False)
    pr.set_active(todos[0]["id"])
    return tmp_path


# ─── resolução do alvo ───────────────────────────────────────────────────────


def test_resolve_por_nome(registro):
    entry, erro = _resolver_projeto("forex-ia")
    assert erro is None and entry["name"] == "forex-ia"


def test_resolve_por_id(registro):
    alvo = [p for p in pr.list_projects(enrich=False) if p["name"] == "forex-ia"][0]
    entry, erro = _resolver_projeto(alvo["id"])
    assert erro is None and entry["id"] == alvo["id"]


def test_resolve_por_caminho(registro):
    entry, erro = _resolver_projeto(str(registro / "projetos" / "forex-ia"))
    assert erro is None and entry["name"] == "forex-ia"


def test_resolve_por_prefixo(registro):
    entry, erro = _resolver_projeto("forex")
    assert erro is None and entry["name"] == "forex-ia"


def test_alvo_inexistente_lista_os_registrados(registro):
    entry, erro = _resolver_projeto("nao-existe")
    assert entry is None
    # a mensagem tem que ser acionável: dizer o que EXISTE
    assert "forex-ia" in erro and "github-radar" in erro


def test_prefixo_ambiguo_nao_escolhe_sozinho(registro):
    """Adivinhar aqui trocaria o projeto errado sem o usuário perceber."""
    d = registro / "projetos" / "forex-antigo"
    d.mkdir()
    pr.add_project(d)

    entry, erro = _resolver_projeto("forex")
    assert entry is None
    assert "mais de um" in erro


# ─── troca do ativo ──────────────────────────────────────────────────────────


def test_use_troca_o_ativo(registro):
    alvo = [p for p in pr.list_projects(enrich=False) if p["name"] == "forex-ia"][0]
    assert pr.get_active() != alvo["id"]

    entry, erro = _resolver_projeto("forex-ia")
    assert erro is None
    assert pr.set_active(entry["id"]) is True
    assert pr.get_active() == alvo["id"]


def test_ativo_persiste_no_disco(registro):
    """O ponteiro é lido por OUTRO processo (serve/Desktop) — tem que gravar."""
    entry, _ = _resolver_projeto("forex-ia")
    pr.set_active(entry["id"])

    dados = json.loads((registro / "bauer_home" / "projects.json").read_text(encoding="utf-8"))
    assert dados["active"] == entry["id"]


# ─── sincronização ao entrar na pasta ────────────────────────────────────────


def test_agent_sincroniza_o_ativo_com_a_pasta(registro, monkeypatch):
    """Entrar na pasta do projeto passa a valer também para Desktop/serve."""
    from bauer.commands.agent_cmd import _resolve_cwd_project

    forex = [p for p in pr.list_projects(enrich=False) if p["name"] == "forex-ia"][0]
    assert pr.get_active() != forex["id"], "pré-condição: outro projeto ativo"

    ws = _resolve_cwd_project(interactive=False, cwd=registro / "projetos" / "forex-ia")

    assert ws is not None and ws.name == "forex-ia"
    assert pr.get_active() == forex["id"], "workspace mudou mas o ponteiro ficou para trás"


def test_sincronizacao_nao_derruba_o_agent(registro, monkeypatch):
    """Falha ao gravar o ponteiro não pode impedir a sessão de começar."""
    from bauer.commands import agent_cmd

    def _explode(*a, **kw):
        raise OSError("registro somente leitura")

    monkeypatch.setattr(pr, "set_active", _explode)

    ws = agent_cmd._resolve_cwd_project(
        interactive=False, cwd=registro / "projetos" / "forex-ia"
    )
    assert ws is not None and ws.name == "forex-ia"


def test_pasta_nao_registrada_nao_mexe_no_ativo(registro):
    """Sem adoção (não-interativo) nada muda — nem workspace, nem ponteiro."""
    from bauer.commands.agent_cmd import _resolve_cwd_project

    antes = pr.get_active()
    solta = registro / "pasta-solta"
    solta.mkdir()

    assert _resolve_cwd_project(interactive=False, cwd=solta) is None
    assert pr.get_active() == antes
