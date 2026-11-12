"""Sprint 82: veredito do /loop, gates fora de git e raiz de projeto aninhada."""

from __future__ import annotations

import os
from pathlib import Path

from bauer import app_verify
from bauer.channel_governance import TurnMetrics, tools_usadas
from bauer.core.kernel.gates.tests import houve_mudanca
from bauer.serve_loop import LOOP_CONFIRM_NUDGE, loop_verdict, run_loop_rounds, strip_verdict


class _Ctx:
    def __init__(self):
        self.messages = []

    def add_user(self, text):
        self.messages.append({"role": "user", "content": text})


class _Budget:
    is_exhausted = False

    def consume_tool_call(self):
        pass


def _run(texts):
    it = iter(texts)
    return run_loop_rounds(goal="g", ctx=_Ctx(), turn_fn=lambda: (next(it), []),
                           budget=_Budget())


def test_verdict_parsing():
    assert loop_verdict("ok\nRESULTADO: CONCLUIDO") == "done"
    assert loop_verdict("ok\n`RESULTADO: PENDENTE`") == "pending"
    assert loop_verdict("Resultado: concluído.") == "done"
    assert loop_verdict("RESULTADO: CONCLUIDO\ndepois\nRESULTADO: PENDENTE") == "pending"
    assert loop_verdict("terminei") is None
    assert strip_verdict("Feito.\nRESULTADO: CONCLUIDO") == "Feito."
    assert "RESULTADO: PENDENTE" in LOOP_CONFIRM_NUDGE


def test_loop_that_stops_without_meeting_the_goal_is_not_completed():
    reason, rounds, text, _ = _run(
        ["Triagem feita.", "Nenhuma tarefa foi concluída.\nRESULTADO: PENDENTE"])
    assert reason == "objetivo_pendente"
    assert rounds == 2
    assert text == "Nenhuma tarefa foi concluída."


def test_loop_with_done_or_missing_verdict_stays_completed():
    assert _run(["Feito.", "Confirmo.\nRESULTADO: CONCLUIDO"])[0] == "completed"
    assert _run(["Feito.", "Confirmo."])[0] == "completed"


def test_tools_usadas_feeds_the_tests_gate_outside_git(tmp_path):
    log = [{"tool": "read_file"}, {"tool": "write_file"}, {"tool": "write_file"}, {}]
    assert tools_usadas(log) == ["read_file", "write_file"]
    result = TurnMetrics().result("ok", log)
    assert result["tools_usadas"] == ["read_file", "write_file"]
    # tmp_path não é repo git: a evidência vem das tools.
    assert houve_mudanca(tmp_path, result) is True
    assert houve_mudanca(tmp_path, TurnMetrics().result("ok", [{"tool": "read_file"}])) is False


def test_single_nested_project_is_the_verification_root(tmp_path):
    proj = tmp_path / "meu-app"
    (proj / "tests").mkdir(parents=True)
    (proj / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    assert app_verify._raiz_do_projeto(tmp_path) == proj

    calls = []

    def runner(cmd, cwd, timeout):
        calls.append((cmd, Path(cwd)))
        return 0, "1 passed"

    res = app_verify.verify_project(tmp_path, runner=runner, which=lambda exe: exe,
                                    install=False, only=["test"])
    assert res.ok and res.stack == "python"
    assert calls and calls[0][1] == proj

    # Duas candidatas: não adivinha.
    other = tmp_path / "outro"
    other.mkdir()
    (other / "package.json").write_text("{}", encoding="utf-8")
    assert app_verify._raiz_do_projeto(tmp_path) == tmp_path


def test_suffixed_venv_is_a_python_candidate(tmp_path):
    exe = tmp_path / ".venv-bauer" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    assert str(exe) in app_verify._candidatos_python(tmp_path)


def test_pytest_does_not_inherit_parent_config(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = \"-n 4\"\n", encoding="utf-8")
    child = tmp_path / "registered" / "proj"
    child.mkdir(parents=True)
    assert app_verify._pytest_isolado(child) == ["--rootdir", ".", "-c", os.devnull]
    (child / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    assert app_verify._pytest_isolado(child) == []
