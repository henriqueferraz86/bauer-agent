"""Testes para CompanyManager e comandos CLI de empresa."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer.cli import app
from bauer.company_manager import CompanyDef, CompanyManager, CompanyManagerError

runner = CliRunner()


# ─── CompanyDef ──────────────────────────────────────────────────────────────

class TestCompanyDef:
    def test_valid_id_ok(self):
        assert CompanyDef.valid_id("acme-corp") is True
        assert CompanyDef.valid_id("my_company123") is True

    def test_valid_id_rejects_uppercase(self):
        assert CompanyDef.valid_id("AcmeCorp") is False

    def test_valid_id_rejects_short(self):
        assert CompanyDef.valid_id("a") is False  # mínimo 2 chars

    def test_valid_id_rejects_spaces(self):
        assert CompanyDef.valid_id("acme corp") is False

    def test_to_dict_minimal(self):
        c = CompanyDef(id="foo", name="Foo")
        d = c.to_dict()
        assert d["id"] == "foo"
        assert d["name"] == "Foo"
        assert "model" not in d      # campos vazios omitidos
        assert "provider" not in d

    def test_to_dict_with_optional(self):
        c = CompanyDef(
            id="foo", name="Foo", model="phi4", provider="ollama",
            departments=["tech"], tools_allowed=["read_file"], agent_prefix="foo",
        )
        d = c.to_dict()
        assert d["model"] == "phi4"
        assert d["provider"] == "ollama"
        assert d["departments"] == ["tech"]
        assert d["tools_allowed"] == ["read_file"]
        assert d["agent_prefix"] == "foo"

    def test_from_dict_round_trip(self):
        c = CompanyDef(id="acme", name="Acme Corp", language="en", context="hello")
        d = c.to_dict()
        c2 = CompanyDef.from_dict(d)
        assert c2.id == "acme"
        assert c2.name == "Acme Corp"
        assert c2.language == "en"
        assert c2.context == "hello"

    def test_from_dict_criado_em_fallback(self):
        d = {"id": "x", "name": "X", "criado_em": "2025-01-01T00:00:00+00:00"}
        c = CompanyDef.from_dict(d)
        assert c.created_at == "2025-01-01T00:00:00+00:00"

    def test_path_helpers(self, tmp_path: Path):
        c = CompanyDef(id="acme", name="Acme")
        assert c.base_dir(tmp_path) == tmp_path / "acme"
        assert c.workspace_dir(tmp_path) == tmp_path / "acme" / "workspace"
        assert c.memory_dir(tmp_path) == tmp_path / "acme" / "memory"
        assert c.agents_file(tmp_path) == tmp_path / "acme" / "agents.yaml"


# ─── CompanyManager CRUD ─────────────────────────────────────────────────────

class TestCompanyManagerCRUD:
    def test_create_builds_structure(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        company = cm.create("acme-corp", "Acme Corp", industry="fintech")

        base = tmp_path / "acme-corp"
        assert base.is_dir()
        assert (base / "workspace").is_dir()
        assert (base / "memory").is_dir()
        assert (base / "company.yaml").is_file()
        assert (base / "agents.yaml").is_file()
        assert company.id == "acme-corp"
        assert company.name == "Acme Corp"

    def test_create_invalid_slug_raises(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        with pytest.raises(CompanyManagerError, match="ID inválido"):
            cm.create("INVALID SLUG", "Test")

    def test_create_duplicate_raises(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        cm.create("acme", "Acme")
        with pytest.raises(CompanyManagerError, match="já existe"):
            cm.create("acme", "Acme 2")

    def test_get_returns_company(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        cm.create("myco", "My Company")
        c = cm.get("myco")
        assert c is not None
        assert c.id == "myco"
        assert c.name == "My Company"

    def test_get_nonexistent_returns_none(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        assert cm.get("nope") is None

    def test_list_empty(self, tmp_path: Path):
        cm = CompanyManager(tmp_path / "empty")
        assert cm.list_companies() == []

    def test_list_returns_all(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        cm.create("alpha-co", "Alpha Corp")
        cm.create("beta-co", "Beta Corp")
        companies = cm.list_companies()
        ids = [c.id for c in companies]
        assert "alpha-co" in ids
        assert "beta-co" in ids

    def test_update_saves_fields(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        cm.create("co", "Co")
        c = cm.get("co")
        assert c is not None
        c.language = "en"
        c.model = "phi4"
        cm.update(c)
        c2 = cm.get("co")
        assert c2 is not None
        assert c2.language == "en"
        assert c2.model == "phi4"

    def test_update_nonexistent_raises(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        with pytest.raises(CompanyManagerError, match="não encontrada"):
            cm.update(CompanyDef(id="ghost", name="Ghost"))

    def test_delete_removes_dir(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        cm.create("todelete", "To Delete")
        assert (tmp_path / "todelete").exists()
        assert cm.delete("todelete") is True
        assert not (tmp_path / "todelete").exists()

    def test_delete_nonexistent_returns_false(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        assert cm.delete("nope") is False


# ─── Active company ───────────────────────────────────────────────────────────

class TestActiveCompany:
    def test_set_and_get_active(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        cm = CompanyManager(tmp_path)
        cm.create("alpha", "Alpha")
        cm.set_active("alpha")

        assert cm.get_active_id() == "alpha"
        assert cm.get_active() is not None
        assert cm.get_active().name == "Alpha"  # type: ignore[union-attr]

    def test_clear_active(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        cm = CompanyManager(tmp_path)
        cm.create("beta", "Beta")
        cm.set_active("beta")
        cm.clear_active()

        assert cm.get_active_id() is None
        assert cm.get_active() is None

    def test_set_active_nonexistent_raises(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        cm = CompanyManager(tmp_path)
        with pytest.raises(CompanyManagerError, match="não encontrada"):
            cm.set_active("ghost")

    def test_get_active_id_empty_file(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company"
        active_file.write_text("   ", encoding="utf-8")
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        cm = CompanyManager(tmp_path)
        assert cm.get_active_id() is None

    def test_get_active_no_file(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company_missing"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        cm = CompanyManager(tmp_path)
        assert cm.get_active_id() is None


# ─── Context injection ────────────────────────────────────────────────────────

class TestContextInjection:
    def test_build_prefix_includes_name_and_context(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        c = CompanyDef(id="x", name="Foobar Inc", context="Setor de saude.")
        prefix = cm.build_system_prompt_prefix(c)
        assert "Foobar Inc" in prefix
        assert "Setor de saude." in prefix

    def test_build_prefix_non_pt_language(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        c = CompanyDef(id="x", name="X", language="en")
        prefix = cm.build_system_prompt_prefix(c)
        assert "en" in prefix

    def test_build_prefix_pt_language_not_mentioned(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        c = CompanyDef(id="x", name="X", language="pt")
        prefix = cm.build_system_prompt_prefix(c)
        assert "Idioma de resposta" not in prefix

    def test_inject_context_prepends(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        c = CompanyDef(id="x", name="X Corp", context="Contexto aqui.")
        original = "Voce e um agente."
        result = cm.inject_context(original, c)
        assert result.startswith("# CONTEXTO DA EMPRESA")
        assert "Contexto aqui." in result
        assert "Voce e um agente." in result
        assert result.index("CONTEXTO") < result.index("Voce e um agente.")


# ─── AgentRegistry integration ───────────────────────────────────────────────

class TestCompanyAgentRegistry:
    def test_get_agent_registry_returns_registry(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        cm.create("myco", "My Co")
        reg = cm.get_agent_registry("myco")
        assert reg is not None
        assert reg.path == tmp_path / "myco" / "agents.yaml"

    def test_get_active_registry_none_when_no_active(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company_none"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)
        cm = CompanyManager(tmp_path)
        assert cm.get_active_registry() is None

    def test_get_active_registry_returns_registry(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)
        cm = CompanyManager(tmp_path)
        cm.create("co", "Co")
        cm.set_active("co")
        reg = cm.get_active_registry()
        assert reg is not None


# ─── CLI commands ─────────────────────────────────────────────────────────────

class TestCompanyCLI:
    def test_company_create(self, tmp_path: Path):
        result = runner.invoke(app, [
            "company", "create", "test-co",
            "--name", "Test Co",
            "--industry", "tech",
            "--dir", str(tmp_path),
            "--no-activate",
        ])
        assert result.exit_code == 0, result.output
        assert "Test Co" in result.output
        assert (tmp_path / "test-co" / "company.yaml").exists()

    def test_company_create_invalid_slug(self, tmp_path: Path):
        result = runner.invoke(app, [
            "company", "create", "INVALID SLUG",
            "--name", "Bad",
            "--dir", str(tmp_path),
            "--no-activate",
        ])
        assert result.exit_code == 1

    def test_company_create_duplicate(self, tmp_path: Path):
        runner.invoke(app, [
            "company", "create", "dup", "--name", "Dup", "--dir", str(tmp_path), "--no-activate",
        ])
        result = runner.invoke(app, [
            "company", "create", "dup", "--name", "Dup2", "--dir", str(tmp_path), "--no-activate",
        ])
        assert result.exit_code == 1

    def test_company_list_empty(self, tmp_path: Path):
        result = runner.invoke(app, ["company", "list", "--dir", str(tmp_path / "empty")])
        assert result.exit_code == 0
        assert "Nenhuma" in result.output

    def test_company_list_shows_companies(self, tmp_path: Path):
        cm = CompanyManager(tmp_path)
        cm.create("alpha", "Alpha Corp")
        cm.create("beta", "Beta Inc")
        result = runner.invoke(app, ["company", "list", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "beta" in result.output

    def test_company_select(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        cm = CompanyManager(tmp_path)
        cm.create("myco", "My Co")

        result = runner.invoke(app, ["company", "select", "myco", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "myco" in result.output

    def test_company_select_nonexistent(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        result = runner.invoke(app, ["company", "select", "ghost", "--dir", str(tmp_path)])
        assert result.exit_code == 1

    def test_company_info(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        cm = CompanyManager(tmp_path)
        cm.create("myco", "My Co", industry="fintech")

        result = runner.invoke(app, ["company", "info", "myco", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "myco" in result.output

    def test_company_info_no_slug_uses_active(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        cm = CompanyManager(tmp_path)
        cm.create("active-co", "Active Co")
        cm.set_active("active-co")

        result = runner.invoke(app, ["company", "info", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "active-co" in result.output

    def test_company_info_no_active_exits(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company_missing2"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        result = runner.invoke(app, ["company", "info", "--dir", str(tmp_path)])
        assert result.exit_code == 1

    def test_company_info_nonexistent(self, tmp_path: Path):
        result = runner.invoke(app, ["company", "info", "ghost", "--dir", str(tmp_path)])
        assert result.exit_code == 1

    def test_company_clear_with_active(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        cm = CompanyManager(tmp_path)
        cm.create("co", "Co")
        cm.set_active("co")

        result = runner.invoke(app, ["company", "clear"])
        assert result.exit_code == 0
        assert "desativada" in result.output

    def test_company_clear_no_active(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_no_active"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        result = runner.invoke(app, ["company", "clear"])
        assert result.exit_code == 0
        assert "Nenhuma" in result.output

    def test_company_delete_with_yes(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        cm = CompanyManager(tmp_path)
        cm.create("todel", "To Del")

        result = runner.invoke(app, [
            "company", "delete", "todel", "--yes", "--dir", str(tmp_path),
        ])
        assert result.exit_code == 0
        assert not (tmp_path / "todel").exists()

    def test_company_delete_nonexistent(self, tmp_path: Path):
        result = runner.invoke(app, [
            "company", "delete", "ghost", "--yes", "--dir", str(tmp_path),
        ])
        assert result.exit_code == 1

    def test_company_delete_also_clears_active(self, tmp_path: Path, monkeypatch):
        active_file = tmp_path / ".bauer_active_company"
        monkeypatch.setattr("bauer.company_manager._ACTIVE_FILE", active_file)

        cm = CompanyManager(tmp_path)
        cm.create("myco", "My Co")
        cm.set_active("myco")

        result = runner.invoke(app, [
            "company", "delete", "myco", "--yes", "--dir", str(tmp_path),
        ])
        assert result.exit_code == 0
        assert cm.get_active_id() is None

    def test_company_personas_all(self):
        result = runner.invoke(app, ["company", "personas"])
        assert result.exit_code == 0
        assert "python" in result.output
        assert "ceo" in result.output
        assert "data-scientist" in result.output

    def test_company_personas_filter(self):
        result = runner.invoke(app, ["company", "personas", "finance"])
        assert result.exit_code == 0
        # Financeiro group should appear
        assert result.exit_code == 0


# ─── PERSONAS catalog ────────────────────────────────────────────────────────

class TestPersonasCatalog:
    def test_minimum_40_personas(self):
        from bauer.agent_registry import PERSONAS
        assert len(PERSONAS) >= 40

    def test_all_personas_have_system_and_description(self):
        from bauer.agent_registry import PERSONAS
        for key, p in PERSONAS.items():
            assert "system" in p, f"Persona '{key}' sem 'system'"
            assert "description" in p, f"Persona '{key}' sem 'description'"
            assert len(p["system"]) > 30, f"Persona '{key}' com system muito curto"

    def test_key_personas_exist(self):
        from bauer.agent_registry import PERSONAS
        required = [
            "python", "devops", "security", "architect",
            "ceo", "cto", "cfo",
            "financial-analyst", "copywriter", "seo",
            "sdr", "customer-success",
            "recruiter", "compliance",
            "data-scientist", "bi-analyst",
            "product-manager", "ux-designer",
        ]
        for key in required:
            assert key in PERSONAS, f"Persona '{key}' nao encontrada"
