"""Testes da integração App Factory ↔ serve (plano 024).

O serve/Desktop passa a: (a) expor as tools app_factory_init/status ao modelo
local (allowlist), e (b) injetar o estado do factory (projeto governado, gate,
docs pendentes) no system prompt, para conduzir o Spec-Driven Development.
"""

from __future__ import annotations

from bauer import app_factory


def test_allowlist_includes_factory_tools():
    from bauer.commands._runtime import _LOCAL_DEFAULT_ALLOWLIST

    assert "app_factory_init" in _LOCAL_DEFAULT_ALLOWLIST
    assert "app_factory_status" in _LOCAL_DEFAULT_ALLOWLIST


def test_section_when_not_governed_points_to_init(tmp_path):
    s = app_factory.system_prompt_section(tmp_path)
    assert isinstance(s, str)
    # workspace vazio → orienta a iniciar via tool, não a inventar docs
    assert "app_factory_init" in s
    assert "APP FACTORY" in s


def test_section_never_raises_on_bad_path():
    # nunca pode quebrar o serve
    assert isinstance(app_factory.system_prompt_section("/caminho/que/nao/existe/xyz"), str)


def test_section_governed_mentions_gate_and_missing_docs(tmp_path):
    project = tmp_path / "minhaapp"
    app_factory.init_project(project, idea="app de teste", stack="", overwrite=True)
    app_factory.set_active_project(tmp_path, project)

    s = app_factory.system_prompt_section(tmp_path)
    assert "projeto governado ativo" in s.lower() or "gate atual" in s.lower()
    assert "minhaapp" in s
    # projeto recém-iniciado tem docs de planejamento pendentes
    assert "pendentes" in s.lower()
