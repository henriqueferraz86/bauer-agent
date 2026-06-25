"""Testes do app_verify (P1.1 — auto-verificação do app gerado).

Runner e `which` injetados → determinístico e CI-safe (não precisa de npm/go reais).
"""
from __future__ import annotations

import json

import pytest

from bauer import app_verify as av


# ---------------------------------------------------------------------------
# Detecção de stack / plano
# ---------------------------------------------------------------------------

class TestPlan:
    def test_node_install_build_test(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"build": "vite build", "test": "vitest"}}),
            encoding="utf-8",
        )
        stack, steps = av.plan_verification(tmp_path)
        names = [n for n, _ in steps]
        assert stack == "node"
        assert names == ["install", "build", "test"]
        assert steps[0][1][:2] == ["npm", "install"]  # sem lock → install

    def test_node_lock_usa_ci(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({"scripts": {}}), encoding="utf-8")
        (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
        _, steps = av.plan_verification(tmp_path)
        assert steps[0][1][:2] == ["npm", "ci"]

    def test_node_placeholder_test_ignorado(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}),
            encoding="utf-8",
        )
        _, steps = av.plan_verification(tmp_path)
        assert "test" not in [n for n, _ in steps]

    def test_python_pyproject_com_tests(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        stack, steps = av.plan_verification(tmp_path)
        names = [n for n, _ in steps]
        assert stack == "python"
        assert names == ["install", "test"]
        assert steps[0][1] == ["pip", "install", "-e", "."]
        assert steps[1][1] == ["pytest", "-q"]

    def test_python_requirements_sem_tests_usa_smoke(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
        _, steps = av.plan_verification(tmp_path)
        names = [n for n, _ in steps]
        assert names == ["install", "smoke"]
        assert steps[0][1] == ["pip", "install", "-r", "requirements.txt"]

    def test_go(self, tmp_path):
        (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
        stack, steps = av.plan_verification(tmp_path)
        assert stack == "go" and steps[0][0] == "build"

    def test_unknown_sem_plano(self, tmp_path):
        (tmp_path / "README.md").write_text("oi", encoding="utf-8")
        stack, steps = av.plan_verification(tmp_path)
        assert stack == "unknown" and steps == []


# ---------------------------------------------------------------------------
# verify_project (runner injetado)
# ---------------------------------------------------------------------------

class TestVerifyProject:
    @staticmethod
    def _node(tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"build": "b", "test": "t"}}), encoding="utf-8",
        )

    _WHICH_ALL = staticmethod(lambda exe: "/usr/bin/" + exe)

    def test_tudo_passa(self, tmp_path):
        self._node(tmp_path)
        runner = lambda cmd, cwd, to: (0, "ok")
        r = av.verify_project(tmp_path, runner=runner, which=self._WHICH_ALL)
        assert r.ok is True
        assert [s.name for s in r.steps] == ["install", "build", "test"]
        assert all(s.ok for s in r.steps)
        assert "✓" in r.summary

    def test_para_na_primeira_falha(self, tmp_path):
        self._node(tmp_path)
        # install ok, build falha → test NÃO roda
        seq = iter([(0, "instalou"), (1, "erro de build TS1234")])
        runner = lambda cmd, cwd, to: next(seq)
        r = av.verify_project(tmp_path, runner=runner, which=self._WHICH_ALL)
        assert r.ok is False
        assert [s.name for s in r.steps] == ["install", "build"]  # parou no build
        assert "TS1234" in r.steps[-1].output

    def test_tool_ausente_no_path(self, tmp_path):
        self._node(tmp_path)
        runner = lambda cmd, cwd, to: (0, "")
        r = av.verify_project(tmp_path, runner=runner, which=lambda e: None)
        assert r.ok is False
        assert r.steps[0].skipped and "não encontrado" in r.steps[0].reason

    def test_install_desativado(self, tmp_path):
        self._node(tmp_path)
        runner = lambda cmd, cwd, to: (0, "ok")
        r = av.verify_project(tmp_path, runner=runner, which=self._WHICH_ALL, install=False)
        assert r.ok is True
        assert r.steps[0].name == "install" and r.steps[0].skipped

    def test_stack_desconhecida(self, tmp_path):
        (tmp_path / "x.txt").write_text("y", encoding="utf-8")
        r = av.verify_project(tmp_path, runner=lambda *a: (0, ""), which=self._WHICH_ALL)
        assert r.ok is False and r.stack == "unknown"
        assert "não detectada" in r.summary.lower() or "nao detectada" in r.summary.lower()

    def test_projeto_inexistente(self, tmp_path):
        r = av.verify_project(tmp_path / "nope", which=self._WHICH_ALL)
        assert r.ok is False


# ---------------------------------------------------------------------------
# Integração via ToolRouter
# ---------------------------------------------------------------------------

class TestToolRouterVerifyApp:
    def test_verify_app_reporta_falha(self, tmp_path, monkeypatch):
        from bauer.tool_router import ToolRouter
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"build": "b"}}), encoding="utf-8",
        )
        # runner que falha o build; which finge tudo instalado
        import bauer.app_verify as _av
        monkeypatch.setattr(_av.shutil, "which", lambda e: "/usr/bin/" + e)
        seq = iter([(0, "instalou"), (1, "BUILD QUEBROU aqui")])
        monkeypatch.setattr(_av, "_default_runner", lambda cmd, cwd, to: next(seq))

        tr = ToolRouter(workspace=tmp_path)
        out = tr.execute(json.dumps({"action": "verify_app", "args": {}}))
        assert "[verify_app]" in out
        assert "BUILD QUEBROU" in out
        assert "verificação falhou" in out or "verificacao falhou" in out or "✗" in out

    def test_verify_app_persiste_resultado(self, tmp_path, monkeypatch):
        """P1.4: verify_app salva .bauer_meta/verify_result.json após rodar."""
        from bauer.tool_router import ToolRouter
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"build": "b"}}), encoding="utf-8",
        )
        import bauer.app_verify as _av
        monkeypatch.setattr(_av.shutil, "which", lambda e: "/usr/bin/" + e)
        monkeypatch.setattr(_av, "_default_runner", lambda cmd, cwd, to: (0, "ok"))

        tr = ToolRouter(workspace=tmp_path)
        tr.execute(json.dumps({"action": "verify_app", "args": {}}))

        result_path = tmp_path / ".bauer_meta" / "verify_result.json"
        assert result_path.is_file(), "verify_result.json deve ser criado"
        data = json.loads(result_path.read_text(encoding="utf-8"))
        assert data["ok"] is True
        assert data["stack"] == "node"

    def test_verify_app_falha_atualiza_resultado(self, tmp_path, monkeypatch):
        """P1.4: resultado ok=False é salvo quando verify falha."""
        from bauer.tool_router import ToolRouter
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"build": "b"}}), encoding="utf-8",
        )
        import bauer.app_verify as _av
        monkeypatch.setattr(_av.shutil, "which", lambda e: "/usr/bin/" + e)
        seq = iter([(0, "ok"), (1, "build error")])
        monkeypatch.setattr(_av, "_default_runner", lambda cmd, cwd, to: next(seq))

        tr = ToolRouter(workspace=tmp_path)
        tr.execute(json.dumps({"action": "verify_app", "args": {}}))

        data = json.loads((tmp_path / ".bauer_meta" / "verify_result.json").read_text())
        assert data["ok"] is False


# ---------------------------------------------------------------------------
# Diagnóstico de falha (P1.3)
# ---------------------------------------------------------------------------

class TestDiagnoseFailure:
    def test_module_not_found_python(self):
        hint = av._diagnose_failure("ModuleNotFoundError: No module named 'flask'", 1)
        assert "pip install" in hint

    def test_cannot_find_module_node(self):
        hint = av._diagnose_failure("Cannot find module 'express'", 1)
        assert "npm install" in hint.lower()

    def test_syntax_error_python(self):
        hint = av._diagnose_failure("SyntaxError: invalid syntax at line 12", 1)
        assert "sintaxe" in hint.lower() or "syntax" in hint.lower()

    def test_rc_127_command_not_found(self):
        hint = av._diagnose_failure("", 127)
        assert "PATH" in hint or "instalad" in hint.lower()

    def test_no_hint_for_generic_error(self):
        hint = av._diagnose_failure("xyzzy unknown error", 1)
        assert hint == ""

    def test_hint_appears_in_summary(self):
        """P1.3: hint de diagnóstico aparece no summary do VerifyResult."""
        root = __import__("pathlib").Path(__import__("tempfile").mkdtemp())
        (root / "package.json").write_text(
            json.dumps({"scripts": {"build": "b"}}), encoding="utf-8"
        )
        seq = iter([(0, "ok"), (1, "ModuleNotFoundError: No module named 'lodash'")])
        r = av.verify_project(
            root,
            runner=lambda cmd, cwd, to: next(seq),
            which=lambda e: "/bin/" + e,
        )
        assert "pip install" in r.summary or "Dependência" in r.summary
