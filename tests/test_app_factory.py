"""Testes da App Factory — Spec-Driven Development gravado no DNA do Bauer.

Cobre: derivação de gates, scaffold idempotente, hash pristino, can_write_code,
Delivery Score objetivo, e o enforcement via ToolRouter.execute().
"""

from __future__ import annotations

import json

import pytest

from bauer import app_factory as af


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fill(project, name, text="conteudo real preenchido " * 20):
    (project / "docs" / name).write_text(f"# {name}\n{text}", encoding="utf-8")


def _fill_all_planning(project):
    for d in af.PLANNING_DOCS:
        _fill(project, d)


# ---------------------------------------------------------------------------
# Governança / marker
# ---------------------------------------------------------------------------


class TestGovernance:
    def test_not_governed_by_default(self, tmp_path):
        assert af.is_governed(tmp_path) is False
        assert af.current_gate(tmp_path) is None

    def test_init_creates_marker_and_docs(self, tmp_path):
        res = af.init_project(tmp_path, idea="Encurtador de URLs", stack="FastAPI")
        assert af.is_governed(tmp_path) is True
        assert (tmp_path / "docs" / af.MARKER_NAME).is_file()
        # 7 planning + 6 delivery + README/.env.example/CI
        assert len(res["written"]) >= 13
        for d in af.PLANNING_DOCS:
            assert (tmp_path / "docs" / d).is_file()

    def test_state_records_idea_and_stack(self, tmp_path):
        af.init_project(tmp_path, idea="Minha ideia", stack="Next.js")
        st = af.load_state(tmp_path)
        assert st["idea"] == "Minha ideia"
        assert st["stack"] == "Next.js"
        assert "pristine_hashes" in st


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


class TestGates:
    def test_discovery_after_init(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        assert af.current_gate(tmp_path) == af.Gate.DISCOVERY

    def test_planning_after_spec_filled(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        _fill(tmp_path, "SPEC.md")
        assert af.current_gate(tmp_path) == af.Gate.PLANNING

    def test_implementation_after_all_planning_docs(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        _fill_all_planning(tmp_path)
        assert af.planning_complete(tmp_path) is True
        assert af.current_gate(tmp_path) >= af.Gate.IMPLEMENTATION

    def test_scaffolded_skeleton_does_not_count_as_filled(self, tmp_path):
        # Só o esqueleto não preenche — gate continua em discovery.
        af.init_project(tmp_path, idea="x")
        assert af.doc_is_filled(tmp_path, "ARCHITECTURE.md") is False
        assert af.current_gate(tmp_path) == af.Gate.DISCOVERY

    def test_missing_planning_docs_listed(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        _fill(tmp_path, "SPEC.md")
        missing = af.missing_planning_docs(tmp_path)
        assert "SPEC.md" not in missing
        assert "ARCHITECTURE.md" in missing


# ---------------------------------------------------------------------------
# can_write_code (enforcement lógico)
# ---------------------------------------------------------------------------


class TestCanWriteCode:
    def test_ungoverned_allows_everything(self, tmp_path):
        ok, _ = af.can_write_code(tmp_path, "app/main.py")
        assert ok is True

    def test_governed_blocks_code_in_discovery(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        ok, reason = af.can_write_code(tmp_path, "app/main.py")
        assert ok is False
        assert "planejamento" in reason.lower()

    def test_governed_allows_docs(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        ok, _ = af.can_write_code(tmp_path, "docs/SPEC.md")
        assert ok is True

    def test_governed_allows_readme_and_env(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        assert af.can_write_code(tmp_path, "README.md")[0] is True
        assert af.can_write_code(tmp_path, ".env.example")[0] is True
        assert af.can_write_code(tmp_path, ".github/workflows/ci.yml")[0] is True

    def test_code_allowed_after_planning_complete(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        _fill_all_planning(tmp_path)
        ok, _ = af.can_write_code(tmp_path, "app/main.py")
        assert ok is True


# ---------------------------------------------------------------------------
# Scaffold
# ---------------------------------------------------------------------------


class TestScaffold:
    def test_idempotent_without_overwrite(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        _fill(tmp_path, "SPEC.md", text="EDITADO PELO USUARIO " * 20)
        # re-scaffold sem overwrite não apaga edição
        af.scaffold_docs(tmp_path, idea="x", overwrite=False)
        content = (tmp_path / "docs" / "SPEC.md").read_text(encoding="utf-8")
        assert "EDITADO PELO USUARIO" in content

    def test_overwrite_restores_skeleton(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        _fill(tmp_path, "SPEC.md", text="EDITADO " * 20)
        af.scaffold_docs(tmp_path, idea="x", overwrite=True)
        content = (tmp_path / "docs" / "SPEC.md").read_text(encoding="utf-8")
        assert "EDITADO" not in content

    def test_pristine_hash_detects_edit(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        assert af.doc_is_filled(tmp_path, "SPEC.md") is False
        _fill(tmp_path, "SPEC.md")
        assert af.doc_is_filled(tmp_path, "SPEC.md") is True

    def test_idea_injected_into_spec(self, tmp_path):
        af.init_project(tmp_path, idea="Plataforma de cursos online")
        spec = (tmp_path / "docs" / "SPEC.md").read_text(encoding="utf-8")
        assert "Plataforma de cursos online" in spec


# ---------------------------------------------------------------------------
# Delivery Score
# ---------------------------------------------------------------------------


class TestDeliveryScore:
    def test_low_score_right_after_init(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        sc = af.delivery_score(tmp_path)
        # README/.env.example scaffoldados contam; docs ainda não preenchidos
        assert sc["score"] < af.DELIVERY_READY_THRESHOLD
        assert sc["ready"] is False
        assert sc["total"] == len(sc["checks"])

    def test_score_rises_when_docs_filled(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        before = af.delivery_score(tmp_path)["score"]
        _fill_all_planning(tmp_path)
        _fill(tmp_path, "SECURITY_CHECKLIST.md")
        _fill(tmp_path, "DEPLOY_CHECKLIST.md")
        _fill(tmp_path, "RUNBOOK.md")
        after = af.delivery_score(tmp_path)["score"]
        assert after > before

    def test_tests_signal_detected(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_smoke.py").write_text("def test_x(): assert True", encoding="utf-8")
        assert af.delivery_score(tmp_path)["checks"]["tests"] is True


# ---------------------------------------------------------------------------
# Integração com ToolRouter (enforcement no DNA)
# ---------------------------------------------------------------------------


class TestToolRouterIntegration:
    def _router(self, tmp_path):
        from bauer.tool_router import ToolRouter
        return ToolRouter(workspace=tmp_path)

    def test_init_tool_starts_governance(self, tmp_path):
        tr = self._router(tmp_path)
        out = tr.execute(json.dumps({
            "action": "app_factory_init",
            "args": {"idea": "Encurtador", "stack": "FastAPI"},
        }))
        assert "app_factory_init" in out
        assert af.is_governed(tmp_path)

    def test_init_requires_idea(self, tmp_path):
        from bauer.tool_router import ToolError
        tr = self._router(tmp_path)
        with pytest.raises(ToolError):
            tr.execute(json.dumps({"action": "app_factory_init", "args": {}}))

    def test_write_code_blocked_before_planning(self, tmp_path):
        tr = self._router(tmp_path)
        tr.execute(json.dumps({"action": "app_factory_init", "args": {"idea": "x"}}))
        out = tr.execute(json.dumps({
            "action": "write_file",
            "args": {"path": "app/main.py", "content": "print(1)"},
        }))
        assert "[App Factory]" in out
        assert not (tmp_path / "app" / "main.py").exists()

    def test_write_doc_allowed_before_planning(self, tmp_path):
        tr = self._router(tmp_path)
        tr.execute(json.dumps({"action": "app_factory_init", "args": {"idea": "x"}}))
        out = tr.execute(json.dumps({
            "action": "write_file",
            "args": {"path": "docs/NOTAS.md", "content": "# notas\n" + "x" * 50},
        }))
        assert "[App Factory]" not in out
        assert (tmp_path / "docs" / "NOTAS.md").exists()

    def test_write_code_allowed_after_planning(self, tmp_path):
        tr = self._router(tmp_path)
        tr.execute(json.dumps({"action": "app_factory_init", "args": {"idea": "x"}}))
        _fill_all_planning(tmp_path)
        out = tr.execute(json.dumps({
            "action": "write_file",
            "args": {"path": "app/main.py", "content": "print(1)"},
        }))
        assert "[App Factory]" not in out
        assert (tmp_path / "app" / "main.py").exists()

    def test_ungoverned_project_unaffected(self, tmp_path):
        # Sem init: comportamento normal do Bauer, sem bloqueio.
        tr = self._router(tmp_path)
        out = tr.execute(json.dumps({
            "action": "write_file",
            "args": {"path": "app/main.py", "content": "print(1)"},
        }))
        assert "[App Factory]" not in out
        assert (tmp_path / "app" / "main.py").exists()

    def test_status_and_score_tools(self, tmp_path):
        tr = self._router(tmp_path)
        tr.execute(json.dumps({"action": "app_factory_init", "args": {"idea": "x"}}))
        status_out = tr.execute(json.dumps({"action": "app_factory_status", "args": {}}))
        assert "gate: discovery" in status_out
        score_out = tr.execute(json.dumps({"action": "app_factory_score", "args": {}}))
        assert "Delivery Score" in score_out


# ---------------------------------------------------------------------------
# Templates / skill
# ---------------------------------------------------------------------------


def test_all_templates_present():
    names = set(af.list_templates())
    for d in (*af.PLANNING_DOCS, *af.DELIVERY_DOCS):
        assert d in names, f"template {d} ausente"
    assert "README.md" in names
    assert ".env.example" in names


def test_skill_yaml_discoverable():
    from pathlib import Path
    p = Path(af.__file__).parent / "data" / "skills" / "coding" / "app-factory.yaml"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "app_factory_init" in text
